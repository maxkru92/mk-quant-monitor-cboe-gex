// GEX Collector v4.2 — Cloudflare Worker
// v4.2: Daily EOD (post-16:15 ET) + OPEN (9:45 ET) AI digests, distinct cron patterns,
//       idempotent per ET-business-day in KV, cached for /eod & /open chat commands.
// v4.1: Workers AI executive summary (Krupp Capital Quantitative Analyst AI persona)
//       synthesizes all 7-symbol GEX data after every cron tick; cached in KV;
//       accessible via /summary (HTTP) and /ai (Telegram chat command).
// v4.0: Open Telegram bot (anyone can message), MenthorQ single-line CSV format
// v3.2: Gamma=0 from CBOE -> approximate via BSM using IV, DTE
// v3.x: Telegram bot integration, 15-minute cron, regime change alerts

const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const SYMBOL_CONFIG = {
  SPX:  { index: true,  label: "S&P 500",      multiplier: 100 },
  NDX:  { index: true,  label: "Nasdaq 100",   multiplier: 100 },
  RUT:  { index: true,  label: "Russell 2000", multiplier: 100 },
  VIX:  { index: true,  label: "VIX",          multiplier: 100 },
  SPY:  { index: false, label: "SPY ETF",      multiplier: 100 },
  QQQ:  { index: false, label: "QQQ ETF",      multiplier: 100 },
  IWM:  { index: false, label: "IWM ETF",      multiplier: 100 },
  GLD:  { index: false, label: "Gold ETF",     multiplier: 100 },
  SLV:  { index: false, label: "Silver ETF",   multiplier: 100 },
  USO:  { index: false, label: "Oil ETF",      multiplier: 100 },
};

const INDEX_SYMBOLS = ["SPX", "NDX", "RUT", "VIX", "OEX", "XEO", "SPXW"];
function isIndex(symbol) { return INDEX_SYMBOLS.includes(symbol.toUpperCase()); }

// ================================================================
// BSM GAMMA
// ================================================================

const SQRT_2PI = Math.sqrt(2 * Math.PI);

function bsmGamma(S, K, sigma, T) {
  if (!sigma || sigma <= 0 || !S || S <= 0 || !K || K <= 0 || T <= 0) return 0;
  try {
    const sqrtT = Math.sqrt(T);
    const d1 = (Math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT);
    const nd1 = Math.exp(-0.5 * d1 * d1) / SQRT_2PI;
    return nd1 / (S * sigma * sqrtT);
  } catch { return 0; }
}

function approxGammaFromIV(S, K, iv, dte) {
  if (!iv || iv <= 0) return 0;
  const T = Math.max(dte, 1) / 365;
  return bsmGamma(S, K, iv / 100, T);
}

// ================================================================
// CBOE SPOT
// ================================================================

async function fetchCBOESpot(symbol) {
  const url = `https://www.cboe.com/education/tools/trade-optimizer/symbol-info/?symbol=${isIndex(symbol) ? "^" : ""}${symbol}`;
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": UA, "Accept": "application/json" },
      cf: { cacheTtl: 120 }
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data?.success || !data?.details) return null;
    const d = data.details;
    const price = parseFloat(d.current_price);
    if (!price || price <= 0) return null;
    return {
      price,
      change: parseFloat(d.price_change) || 0,
      changePct: parseFloat(d.price_change_percent) || 0,
      iv30: parseFloat(d.iv30) || 0,
      iv30Change: parseFloat(d.iv30_change) || 0,
      prevClose: parseFloat(d.prev_day_close) || 0,
      source: "cboe",
      expirations: data.expirations || []
    };
  } catch { return null; }
}

// ================================================================
// CBOE OPTIONS CHAIN (now also extracts 0DTE separately)
// ================================================================

async function fetchCBOEChain(symbol) {
  const prefix = isIndex(symbol) ? "_" : "";
  const url = `https://cdn.cboe.com/api/global/delayed_quotes/options/${prefix}${symbol}.json`;
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": UA, "Accept": "application/json" },
      cf: { cacheTtl: 300 }
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data?.data) return null;
    const d = data.data;
    const spot = parseFloat(d.current_price) || 0;
    const options = d.options;
    if (!options || !Array.isArray(options) || options.length === 0) return null;

    const allEntries = [];
    for (const opt of options) {
      const sym = opt.option || opt.symbol || "";
      if (!sym) continue;
      const match = sym.match(/^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$/);
      if (!match) continue;
      const [, ticker, dateStr, type, strikeRaw] = match;
      const strike = parseInt(strikeRaw) / 1000;
      const expiry = `20${dateStr.slice(0,2)}-${dateStr.slice(2,4)}-${dateStr.slice(4,6)}`;

      const expDate = new Date(expiry);
      const now = new Date();
      const dte = Math.max(0, Math.floor((expDate - now) / (1000 * 60 * 60 * 24)));

      let gamma = parseFloat(opt.gamma) || 0;
      const iv = parseFloat(opt.iv) || 0;
      // BSM fallback when CBOE gamma is missing or zero.
      // CBOE returns IV in percent (e.g. 15.5 means 15.5% — NOT 15.5 as a decimal).
      // approxGammaFromIV handles the /100 scaling and the T = max(dte,1)/365 floor.
      if (gamma <= 0 && iv > 0) {
        gamma = approxGammaFromIV(spot, strike, iv, Math.max(dte, 1));
      }
      allEntries.push({
        strike, expiry, dte, type,
        oi: parseInt(opt.open_interest) || 0,
        volume: parseInt(opt.volume) || 0,
        iv, gamma,
        delta: parseFloat(opt.delta) || 0,
        bid: parseFloat(opt.bid) || 0,
        ask: parseFloat(opt.ask) || 0,
      });
    }
    if (allEntries.length === 0) return null;

    const futureEntries = allEntries.filter(e => e.dte >= 0);
    if (futureEntries.length === 0) return null;

    // --- Normal levels: aggregate across ALL listed expirations ---
    // aggregateStrikes already correctly sums OI and OI-weighted gamma across every entry
    // it receives, so passing the full futureEntries produces a strike-level view of the
    // entire daily/weekly/monthly chain (Call Wall, Put Support, HVL reflect the whole book).
    const allStrikes = aggregateStrikes(futureEntries);

    // --- 0DTE Levels: next trading day in US Eastern Time ---
    // Settings: today if ET date is Mon-Fri AND current ET time is < 16:00; else next weekday.
    // Uses America/New_York (DST-aware) so it stays correct across EST/EDT transitions.
    function getNextTradingDayET() {
      const etStr = new Date().toLocaleString("en-US", { timeZone: "America/New_York", hour12: false });
      const [datePart, timePart] = etStr.split(", ");
      const [month, day, year] = datePart.split("/").map(Number);
      const [hh] = timePart.split(":").map(Number);
      const et = new Date(year, month - 1, day);
      const weekday = et.getDay(); // 0=Sun, 6=Sat
      const pastClose = weekday >= 1 && weekday <= 5 && hh >= 16;
      if (weekday === 0 || weekday === 6 || pastClose) {
        do {
          et.setDate(et.getDate() + 1);
        } while (et.getDay() === 0 || et.getDay() === 6);
      }
      return `${et.getFullYear()}-${String(et.getMonth() + 1).padStart(2, '0')}-${String(et.getDate()).padStart(2, '0')}`;
    }
    const nextTdExpiry = getNextTradingDayET();
    const nextTdEntries = futureEntries.filter(e => e.expiry === nextTdExpiry);
    const hasZeroDTE = nextTdEntries.length > 0;
    const zeroDteExpiry = hasZeroDTE ? nextTdExpiry : null;
    const zeroDteStrikes = hasZeroDTE ? aggregateStrikes(nextTdEntries) : [];

    // dte to next trading day (calendar-day delta, ET-equivalent midnight midpoint)
    const nextTdMidnightUTC = new Date(nextTdExpiry + "T00:00:00Z").getTime();
    const todayMidnightUTC = new Date(new Date().toISOString().split("T")[0] + "T00:00:00Z").getTime();
    const dteToNextTd = Math.max(0, Math.round((nextTdMidnightUTC - todayMidnightUTC) / (1000 * 60 * 60 * 24)));

    return {
      frontStrikes: allStrikes,         // legacy alias (downstream code expects this)
      allStrikes,                       // explicit field
      spot, source: "cboe",
      fetchedAt: new Date().toISOString(),
      totalOptions: options.length,
      frontExpiry: nextTdExpiry,        // chart anchor for "next trading day"
      dte: dteToNextTd,
      zeroDteStrikes, zeroDteExpiry,
      allExpiries: [...new Set(futureEntries.map(e => e.expiry))].sort(),
    };
  } catch (e) {
    console.log(`[CBOE CHAIN] ${symbol} error: ${e.message}`);
    return null;
  }
}

function aggregateStrikes(entries) {
  const callAgg = new Map();
  const putAgg = new Map();
  for (const e of entries) {
    if (e.type === "C") {
      const ex = callAgg.get(e.strike) || { strike: e.strike, oi: 0, volume: 0, gammaOI: 0, ivSum: 0, ivCount: 0 };
      ex.oi += e.oi; ex.volume += e.volume; ex.gammaOI += e.gamma * e.oi;
      if (e.iv > 0) { ex.ivSum += e.iv; ex.ivCount++; }
      callAgg.set(e.strike, ex);
    } else {
      const ex = putAgg.get(e.strike) || { strike: e.strike, oi: 0, volume: 0, gammaOI: 0, ivSum: 0, ivCount: 0 };
      ex.oi += e.oi; ex.volume += e.volume; ex.gammaOI += e.gamma * e.oi;
      if (e.iv > 0) { ex.ivSum += e.iv; ex.ivCount++; }
      putAgg.set(e.strike, ex);
    }
  }
  const allStrikes = new Set([...callAgg.keys(), ...putAgg.keys()]);
  const out = [];
  for (const strike of allStrikes) {
    const c = callAgg.get(strike);
    const p = putAgg.get(strike);
    const callGamma = c && c.oi > 0 ? c.gammaOI / c.oi : 0;
    const putGamma = p && p.oi > 0 ? p.gammaOI / p.oi : 0;
    const callIV = c && c.ivCount > 0 ? c.ivSum / c.ivCount : 0;
    const putIV = p && p.ivCount > 0 ? p.ivSum / p.ivCount : 0;
    out.push({
      strike, expiry: entries[0]?.expiry, dte: entries[0]?.dte,
      callOI: c?.oi || 0, putOI: p?.oi || 0,
      callGamma, putGamma, callIV, putIV,
      callVolume: c?.volume || 0, putVolume: p?.volume || 0,
    });
  }
  out.sort((a, b) => a.strike - b.strike);
  return out;
}

// ================================================================
// YAHOO FALLBACK + INTRADAY (1D Min/Max)
// ================================================================

async function fetchYahooSpot(symbol) {
  const yahooSymbol = isIndex(symbol) ? `^${symbol}` : symbol;
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}?interval=15m&range=1d`;
  try {
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) return null;
    const data = await res.json();
    const closes = data?.chart?.result?.[0]?.indicators?.quote?.[0]?.close;
    if (!closes) return null;
    for (let i = closes.length - 1; i >= 0; i--) {
      if (closes[i] > 0) return { price: closes[i], source: "yahoo" };
    }
    return null;
  } catch { return null; }
}

async function fetchIntradayRange(symbol) {
  const yahooSymbol = isIndex(symbol) ? `^${symbol}` : symbol;
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}?interval=5m&range=1d`;
  try {
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) return null;
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    if (!result) return null;
    const highs = result.indicators?.quote?.[0]?.high || [];
    const lows = result.indicators?.quote?.[0]?.low || [];
    const validHighs = highs.filter(v => typeof v === "number" && v > 0);
    const validLows = lows.filter(v => typeof v === "number" && v > 0);
    if (validHighs.length === 0 || validLows.length === 0) return null;
    return {
      min: Math.min(...validLows),
      max: Math.max(...validHighs),
      source: "yahoo"
    };
  } catch { return null; }
}

async function fetchSpot(symbol) {
  let spot = await fetchCBOESpot(symbol);
  if (spot) return spot;
  console.log(`[SPOT] ${symbol} -> Yahoo fallback`);
  const yahoo = await fetchYahooSpot(symbol);
  if (yahoo) return yahoo;
  throw new Error(`FETCH_ERROR: no spot for ${symbol}`);
}

async function fetchChain(symbol, spotPrice, iv30) {
  let chain = await fetchCBOEChain(symbol);
  if (chain && chain.frontStrikes && chain.frontStrikes.length > 0) return chain;
  console.log(`[CHAIN] ${symbol} -> BSM synthetic`);
  const iv = iv30 ? iv30 / 100 : 0.15;
  const syn = generateSyntheticChain(spotPrice, iv, symbol);
  if (syn) return syn;
  throw new Error(`FETCH_ERROR: no chain for ${symbol}`);
}

function generateSyntheticChain(spot, iv, underlying) {
  if (!spot || spot <= 0 || !iv || iv <= 0) return null;
  const strikes = [];
  const atmStrike = Math.round(spot / 5) * 5;
  const minStrike = atmStrike - 150;
  const maxStrike = atmStrike + 150;
  for (let k = minStrike; k <= maxStrike; k += 5) {
    const distPct = Math.abs(k - atmStrike) / spot;
    const baseOI = 80000 * Math.exp(-distPct * distPct * 200);
    const noise = 0.8 + Math.random() * 0.4;
    const totalOI = Math.round(baseOI * noise);
    const callOI = Math.round(totalOI * (0.42 + Math.random() * 0.06));
    const putOI = Math.round(totalOI * (0.55 + Math.random() * 0.03));
    const skew = distPct * 0.3;
    const callIV = iv * (1 + skew);
    const putIV = iv * (1 + skew + 0.05);
    const T = 7 / 252;
    const callGamma = bsmGamma(spot, k, callIV, T);
    const putGamma = bsmGamma(spot, k, putIV, T);
    strikes.push({
      strike: k, expiry: "synthetic", dte: 7,
      callOI: Math.max(100, callOI), putOI: Math.max(100, putOI),
      callGamma: Math.max(0.0001, callGamma), putGamma: Math.max(0.0001, putGamma),
      callIV, putIV,
    });
  }
  return {
    frontStrikes: strikes, zeroDteStrikes: [], zeroDteExpiry: null,
    spot, source: "bsm-synthetic", fetchedAt: new Date().toISOString(),
    frontExpiry: "synthetic", dte: 7, totalOptions: strikes.length, allExpiries: ["synthetic"]
  };
}

// ================================================================
// GEX COMPUTATION
// ================================================================

function computeGEX(strikes, spot) {
  let netGEX = 0;
  let maxCallGEX = 0, maxPutGEX = 0;
  let maxCallStrike = spot, maxPutStrike = spot;
  const topCalls = [], topPuts = [];

  for (const s of strikes) {
    // GEX formula: Gamma * OI * Spot^2 / 100 (per 1% move)
    const callGEX = s.callGamma * s.callOI * spot * spot / 100;
    const putGEX = s.putGamma * s.putOI * spot * spot / 100;
    const strikeNetGEX = callGEX - putGEX;
    netGEX += strikeNetGEX;

    if (callGEX > maxCallGEX) { maxCallGEX = callGEX; maxCallStrike = s.strike; }
    if (putGEX > maxPutGEX) { maxPutGEX = putGEX; maxPutStrike = s.strike; }

    // Only push strikes with actual open interest — OI=0 strikes (e.g. undisplayed 0DTE
    // or far-OTM strikes) produce meaningless "top" listings of deep-OTM strikes.
    if (s.callOI > 0) topCalls.push({ strike: s.strike, oi: s.callOI, gex: callGEX });
    if (s.putOI > 0) topPuts.push({ strike: s.strike, oi: s.putOI, gex: putGEX });
  }

  topCalls.sort((a, b) => b.gex - a.gex);
  topPuts.sort((a, b) => b.gex - a.gex);

  // Top 10 by absolute net GEX (positive or negative distortion).
  // Skip strikes with zero net contribution so we don't surface deep-OTM noise.
  const allNet = strikes
    .map(s => ({
      strike: s.strike,
      netGex: s.callGamma * s.callOI * spot * spot / 100 - s.putGamma * s.putOI * spot * spot / 100
    }))
    .filter(s => Math.abs(s.netGex) > 0);
  allNet.sort((a, b) => Math.abs(b.netGex) - Math.abs(a.netGex));
  const topNetGex = allNet.slice(0, 10);

  return {
    netGEX,
    regime: netGEX > 0 ? "POSITIVE_GAMMA" : netGEX < 0 ? "NEGATIVE_GAMMA" : "NEUTRAL",
    callWall: { strike: maxCallStrike, gex: maxCallGEX },
    putSupport: { strike: maxPutStrike, gex: maxPutGEX },
    hvl: Math.round((maxCallStrike + maxPutStrike) / 2 * 100) / 100,
    topCalls: topCalls.slice(0, 10),
    topPuts: topPuts.slice(0, 10),
    topNetGex,
    strikeCount: strikes.length
  };
}

function normalizeGEX(value) {
  return Math.max(-1, Math.min(1, value / 1e11));
}

function detectRegimeChange(prev, curr) {
  if (!prev) return { changed: false, prevRegime: null, currRegime: null, gexDeltaPercent: 0 };

  // `prev` is the saved `result` object (has `netGex` rounded to 2dp).
  // `curr` is the raw `gex` object (has `netGEX` unrounded). Align names explicitly — fall
  // back to either spelling so an old cache value with the wrong key still works.
  const prevNet = Number(prev?.netGex ?? prev?.netGEX ?? 0);
  const currNet = Number(curr?.netGEX ?? curr?.netGex ?? 0);
  const delta = Math.abs(currNet - prevNet);

  // 15% of |prevNet|, with a sane floor to avoid firing on tiny deltas when |prevNet| is small.
  const threshold = Math.max(Math.abs(prevNet) * 0.15, 1e9);

  // Use the same regime names as computeGEX so the alerted reason is consistent with
  // the rest of the report (`POSITIVE_GAMMA` / `NEGATIVE_GAMMA` / `NEUTRAL`).
  const categorize = (n) => n > 0 ? "POSITIVE_GAMMA" : n < 0 ? "NEGATIVE_GAMMA" : "NEUTRAL";
  const prevRegime = categorize(prevNet);
  const currRegime = categorize(currNet);

  // Alert only when the regime CATEGORY changes AND the delta is large enough.
  // Without the category guard, identical-value ticks (or stale cache values) trigger the
  // alert even when nothing changed.
  const changed = prevRegime !== currRegime && delta > threshold;

  const gexDeltaPercent = prevNet !== 0 ? Math.round((currNet - prevNet) / Math.abs(prevNet) * 10000) / 100 : 0;
  return { changed, prevRegime, currRegime, gexDeltaPercent };
}

function formatGex(value) {
  if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-cache" }
  });
}

// ================================================================
// FORMATTERS
// ================================================================

function formatTelegramGexReport(result) {
  const sign = result.spotChangePct >= 0 ? "+" : "";
  const regimeEmoji = result.regime === "POSITIVE_GAMMA" ? "🟢" : result.regime === "NEGATIVE_GAMMA" ? "🔴" : "⚪";
  const changeLine = result.spotChangePct !== null && result.spotChangePct !== undefined
    ? ` (${sign}${result.spotChangePct.toFixed(2)}%)`
    : "";

  const lines = [
    `*${result.symbol}* | GEX Report`,
    `━━━━━━━━━━━━━━━━━━━━━`,
    `💰 Spot: *${result.spot.toFixed(2)}*${changeLine}`,
    `📊 IV30: ${result.iv30 !== null && result.iv30 !== undefined ? result.iv30.toFixed(2) + "%" : "N/A"} | Regime: ${regimeEmoji} ${result.regime}`,
    `📈 Net GEX: *${result.netGexFormatted}*`,
    ``,
    `🔵 Call Wall: *${result.callWall.strike}* (${result.callWall.gex})`,
    `🔴 Put Support: *${result.putSupport.strike}* (${result.putSupport.gex})`,
    `⚖️ HVL: *${result.hvl}*`,
    ``,
    `📅 Aggregate: ${result.expiriesCount ?? '?'} expirations | 0DTE: ${result.frontExpiry} (DTE: ${result.dte}) | Strikes: ${result.strikeCount}`,
    `📡 Source: ${result.chainSource}`,
  ];

  if (result.dayMin && result.dayMax) {
    lines.push(`📊 1D Min: ${result.dayMin.toFixed(2)} | 1D Max: ${result.dayMax.toFixed(2)}`);
  }

  if (result.zeroDte && result.zeroDte.callResistance) {
    lines.push(``);
    lines.push(`🔥 *0DTE Levels:*`);
    lines.push(`  CR: ${result.zeroDte.callResistance} | PS: ${result.zeroDte.putSupport} | HVL: ${result.zeroDte.hvl}`);
  }

  if (result.topCallStrikes && result.topCallStrikes.length > 0) {
    lines.push(`\n🔵 Top Call GEX:`);
    for (const s of result.topCallStrikes.slice(0, 3)) {
      lines.push(`  ${s.strike}: ${s.gex} (OI: ${s.oi.toLocaleString()})`);
    }
  }
  if (result.topPutStrikes && result.topPutStrikes.length > 0) {
    lines.push(`\n🔴 Top Put GEX:`);
    for (const s of result.topPutStrikes.slice(0, 3)) {
      lines.push(`  ${s.strike}: ${s.gex} (OI: ${s.oi.toLocaleString()})`);
    }
  }
  lines.push(`\n━━━━━━━━━━━━━━━━━━━━━`);
  lines.push(`_Krupp Capital Quantitative Desk_`);
  lines.push(`_Precision in Chaos, Alpha in Variance_`);
  return lines.join("\n");
}

// MenthorQ single-line CSV format (matches menthorq's "Gamma Levels EOD" output)
// Example: $SPX: Call Resistance, 7600, Put Support, 7300, HVL, 7495, 1D Min, 7451.62, 1D Max, 7580.18, Call Resistance 0DTE, 7550, Put Support 0DTE, 7475, HVL 0DTE, 7530, Gamma Wall 0DTE, 7550, GEX 1, 7500, GEX 2, 7575, ...
function formatMenthorQGexReport(result) {
  const f2 = (v) => (v !== null && v !== undefined && !isNaN(v)) ? Number(v).toFixed(2) : "N/A";
  const fInt = (v) => (v !== null && v !== undefined && !isNaN(v)) ? String(Math.round(Number(v))) : "N/A";

  const parts = [`$${result.symbol}:`];

  // Front expiry block
  parts.push("Call Resistance", fInt(result.callWall?.strike));
  parts.push("Put Support", fInt(result.putSupport?.strike));
  parts.push("HVL", f2(result.hvl));

  // 1D Min/Max
  parts.push("1D Min", f2(result.dayMin));
  parts.push("1D Max", f2(result.dayMax));

  // 0DTE block
  if (result.zeroDte && result.zeroDte.callResistance) {
    parts.push("Call Resistance 0DTE", fInt(result.zeroDte.callResistance));
    parts.push("Put Support 0DTE", fInt(result.zeroDte.putSupport));
    parts.push("HVL 0DTE", f2(result.zeroDte.hvl));
    // Gamma Wall 0DTE = top absolute net GEX strike in 0DTE subset
    parts.push("Gamma Wall 0DTE", fInt(result.zeroDte.gammaWall));
  } else {
    parts.push("Call Resistance 0DTE", "N/A");
    parts.push("Put Support 0DTE", "N/A");
    parts.push("HVL 0DTE", "N/A");
    parts.push("Gamma Wall 0DTE", "N/A");
  }

  // GEX 1..10 from topNetGex on front expiry (guard against missing/zero strikes → "N/A")
  const top = (result.topNetGexStrikes && result.topNetGexStrikes.length > 0)
    ? result.topNetGexStrikes
    : [];
  for (let i = 0; i < 10; i++) {
    const s = top[i];
    parts.push(`GEX ${i + 1}`, (s && Number(s) > 0) ? fInt(s) : "N/A");
  }

  return parts.join(", ");
}

// ================================================================
// EXECUTIVE SUMMARY (Workers AI — Krupp Capital Quant persona)
// ================================================================

// Primary is fast on the Free tier (~2-4s, fits 14KB JSON context easily).
// Fallback is ultra-fast and returns short structured responses when primary is slow.
const AI_MODEL_PRIMARY = "@cf/meta/llama-3.1-8b-instruct";
const AI_MODEL_FALLBACK = "@cf/meta/llama-3.2-3b-instruct";
const AI_TIMEOUT_MS = 8000;
const AI_MAX_TOKENS = 280;
const AI_CACHE_KEY = "gex:summary:latest";
const AI_MAX_PAYLOAD_KB = 12;

// Daily-digest keys + cron-pattern strings. The scheduled() handler uses event.cron
// to dispatch: EOD pattern OR OPEN pattern runs runDailyDigest(); * * * * * still runs
// the standard 15-min collection + per-symbol broadcasts.
const EOD_CACHE_KEY = "gex:eod:latest";
const OPEN_CACHE_KEY = "gex:open:latest";
const EOD_LAST_RUN_KEY = "gex:eod:lastRun";     // YYYY-MM-DD of last successful EOD run
const OPEN_LAST_RUN_KEY = "gex:open:lastRun";   // YYYY-MM-DD of last successful OPEN run
const EOD_CRON_PATTERN = "30 20,21 * * 1-5";    // ~16:15 ET on weekdays (covers EDT + EST)
const OPEN_CRON_PATTERN = "45 13,14 * * 1-5";   // ~9:45 ET on weekdays (covers EDT + EST)

// Strip bulky arrays (top 10 strikes, all expirations, intraday ranges) so the
// prompt fits well under the 8k Llama 3.1 context. We only need the fields
// the AI uses to detect divergences and call out interesting/dangerous structure.
function minimizeDataForAI(results) {
  return (results || [])
    .filter(r => r && !r.error && r.symbol)
    .map(r => {
      const zero = r.zeroDte && typeof r.zeroDte === "object" ? r.zeroDte : {};
      const cw = r.callWall && typeof r.callWall === "object" ? r.callWall : {};
      const ps = r.putSupport && typeof r.putSupport === "object" ? r.putSupport : {};
      return {
        symbol: r.symbol,
        spot: r.spot,
        changePct: r.spotChangePct,
        regime: r.regime,
        regimeChanged: !!r.regimeChanged,
        netGex: r.netGexFormatted,
        callWall: cw.strike,
        callWallGapPct: cw.distance,
        putSupport: ps.strike,
        putSupportGapPct: ps.distance,
        hvl: r.hvl,
        iv30: r.iv30 ?? null,
        zeroDteCR: zero.callResistance ?? null,
        zeroDtePS: zero.putSupport ?? null,
        ts: r.timestamp,
      };
    });
}

// Build the prompt envelope. Hedged against the plain-text constraint imposed by
// the earlier parse_mode=Markdown removal (incident 2026-07-16). Hard rules:
//
//   * NO asterisks (*), underscores (_), backticks (`), or fenced code blocks.
//   * Emojis are allowed and encouraged.
//   * Max 6 sentences; line breaks between sections.
//   * Focus on cross-symbol regime DIVERGENCES, call-wall overhang, and put-support asymmetry.
//   * Voice = senior options market maker / quant analyst.
function buildAnalystPrompt(minimizedResults) {
  const sysPrompt =
    "You are the Krupp Capital Quantitative Analyst AI, an institutional options market maker voice. " +
    "You will receive real-time GEX (Gamma Exposure) snapshot data for several underlyings. " +
    "Produce a brief EXECUTIVE SUMMARY for a professional trading desk.\n\n" +
    "HARD FORMAT RULES:\n" +
    "- Plain text only. NO markdown asterisks (*), NO underscores (_), NO backticks, NO code blocks.\n" +
    "- Emojis are allowed (📌, 📈, 📉, 🔴, 🟢, ⚖️, 🔥, ⚠️, 🎯).\n" +
    "- Maximum 6 sentences total. Use line breaks between sections.\n" +
    "- Section 1: one-line OVERVIEW of broad market structure.\n" +
    "- Section 2: DIVERGENCES — any symbol whose regime breaks the consensus.\n" +
    "- Section 3: BOTTOM LINE — one sentence on the expected intraday path or key watch level.\n" +
    "- Focus on cross-symbol regime divergence, VIX vs equity direction, call-wall overhang, and put-support asymmetry.\n" +
    "- Be analytical, concise, confident. No filler like 'based on the data provided'. " +
    "Skip preamble; start directly with the OVERVIEW line.";

  const userBlock =
    "GEX SNAPSHOT (real-time):\n" +
    JSON.stringify({
      as_of: new Date().toISOString(),
      underlying_count: minimizedResults.length,
      data: minimizedResults,
    });

  return {
    messages: [
      { role: "system", content: sysPrompt },
      { role: "user", content: userBlock + "\n\nProduce the executive summary now." },
    ],
  };
}

// Strip residual markdown that LLMs at temp ~0.15 still leak ~5% of the time.
// parse_mode is disabled since incident 2026-07-16 (commit 8617fd2), so any leaked
// * _ ` would render as literal asterisks in Telegram. Sanitize at the boundary.
function sanitizeAiText(text) {
  return String(text || "")
    .replace(/\*+/g, "")
    .replace(/__+/g, "")
    .replace(/`+/g, "")
    .trim();
}

// YYYY-MM-DD of the current date in America/New_York (DST-aware). Needed because cron
// runs in UTC, so naive `new Date().toISOString().slice(0,10)` would be the wrong
// calendar day during the rollover window in late afternoon ET.
function todayEtDate() {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const y = parts.find(p => p.type === "year").value;
    const m = parts.find(p => p.type === "month").value;
    const d = parts.find(p => p.type === "day").value;
    return `${y}-${m}-${d}`;
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

async function callWorkersAIWithTimeout(env, model, prompt) {
  // Promise.race ensures we never block the cron past AI_TIMEOUT_MS even if the
  // model hangs (free-tier Workers AI can occasionally queue).
  const inflight = env.AI.run(model, {
    messages: prompt.messages,
    temperature: 0.15,
    max_tokens: AI_MAX_TOKENS,
  }).then(raw => {
    // Workers AI binding shape: { response: "..." } on most chat models.
    // Older / alternate names: result.response. Be tolerant.
    if (typeof raw === "string") return raw;
    return raw?.response || raw?.result?.response || raw?.text || "";
  });

  let to;
  const timeout = new Promise((_, rej) => { to = setTimeout(() => rej(new Error("ai_timeout")), AI_TIMEOUT_MS); });
  try {
    return await Promise.race([inflight, timeout]);
  } finally { clearTimeout(to); }
}

async function generateExecutiveSummary(results, env) {
  if (!env.AI) return { text: null, model: null, latencyMs: 0, reason: "no_ai_binding" };
  const minimized = minimizeDataForAI(results);
  if (minimized.length === 0) return { text: null, model: null, latencyMs: 0, reason: "no_results" };

  const prompt = buildAnalystPrompt(minimized);
  const startMs = Date.now();

  // Primary model first.
  try {
    const text = await callWorkersAIWithTimeout(env, AI_MODEL_PRIMARY, prompt);
    if (text && text.length >= 30) {
      return { text: sanitizeAiText(text), model: AI_MODEL_PRIMARY, latencyMs: Date.now() - startMs };
    }
    console.warn(`[AI] primary ${AI_MODEL_PRIMARY} returned empty/short text (len=${text?.length})`);
  } catch (e1) {
    console.warn(`[AI] primary ${AI_MODEL_PRIMARY} failed: ${e1.message}. Trying fallback.`);
  }

  // Fallback (smaller model, faster, less depth).
  try {
    const text = await callWorkersAIWithTimeout(env, AI_MODEL_FALLBACK, prompt);
    if (text && text.length >= 30) {
      return { text: sanitizeAiText(text), model: AI_MODEL_FALLBACK, latencyMs: Date.now() - startMs };
    }
  } catch (e2) {
    console.error(`[AI] both models failed: primary+fallback=${e2.message}`);
    return { text: null, model: null, latencyMs: Date.now() - startMs, reason: `both_failed` };
  }
  return { text: null, model: null, latencyMs: Date.now() - startMs, reason: "empty_response" };
}

async function broadcastExecutiveSummary(text, env) {
  if (!text || text.length < 30) return 0;

  const header = `🧠 KRUPP CAPITAL AI — EXECUTIVE SUMMARY\n${"━".repeat(20)}\n`;
  const footer =
    `\n${"━".repeat(20)}\n` +
    `_Krupp Capital Quantitative Desk — AI synthesis_\n` +
    `_Precision in Chaos, Alpha in Variance_`;
  const fullText = header + text + footer;

  // Build union of subscribers across ALL active symbols (autoSubscribe signs the
  // user up to every symbol, so the list is usually the same — but union safely
  // catches users who manually /subscribe to only a subset).
  const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
  const union = new Set();
  for (const sym of symbols) {
    const list = await env.GEX_KV.get(`gex:subs:${sym}`, "json") || [];
    for (const cid of list) union.add(String(cid));
  }
  if (env.TELEGRAM_CHAT_ID) union.add(String(env.TELEGRAM_CHAT_ID));

  // Check format preferences — menthorq subscribers get a CSV-flavored summary; standard
  // subscribers get the rich layout. Default to standard layout if no preference set.
  const fmtMap = {};
  await Promise.all([...union].map(async cid => { fmtMap[cid] = await getUserFormat(cid, env); }));

  const standardRecipients = [...union].filter(cid => fmtMap[cid] !== "menthorq");
  if (standardRecipients.length === 0) return 0;
  await chunkedSend(standardRecipients, cid => sendTelegramMessage(cid, fullText, env));
  return standardRecipients.length;
}

// ================================================================
// DAILY DIGESTS (EOD + OPEN) — Krupp Capital Quant Analyst AI
// ================================================================

function buildEodPrompt(intraResults) {
  const sysPrompt =
    "You are the Krupp Capital Quantitative Analyst AI. Provide an EOD RECAP for a professional trading desk based on the closing GEX data.\n" +
    "HARD FORMAT RULES:\n" +
    "- Plain text only. NO markdown asterisks (*), NO underscores (_), NO backticks.\n" +
    "- Emojis are allowed (🌅, 📊, 🔥, 📌, ⚠️, 🔴, 🟢).\n" +
    "- Maximum 8 sentences total. Use line breaks between sections.\n" +
    "- Section 1: EOD REGIME STATE — summarize final net GEX positioning across all 7 symbols.\n" +
    "- Section 2: KEY LEVELS — primary call walls and put supports that closed as the dominant pinning points.\n" +
    "- Section 3: VOL SURFACE NOTE — any notable IV30 readings or late-day vol shifts.\n" +
    "- Section 4: NEXT-SESSION OUTLOOK — one critical dynamic to watch at tomorrow's 9:30 AM ET print.\n" +
    "Be analytical, concise, and confident. No filler phrases.";

  const userPayload = JSON.stringify({
    type: "EOD",
    as_of: new Date().toISOString(),
    et_session_close: true,
    data: intraResults,
  });
  return {
    messages: [
      { role: "system", content: sysPrompt },
      { role: "user", content: userPayload + "\n\nProduce the EOD recap now." },
    ],
  };
}

function buildOpenPrompt(intraResults, priorEodSnapshot) {
  const sysPrompt =
    "You are the Krupp Capital Quantitative Analyst AI. Provide a MORNING UPDATE comparing today's intraday GEX against yesterday's EOD snapshot.\n" +
    "HARD FORMAT RULES:\n" +
    "- Plain text only. NO markdown asterisks (*), NO underscores (_), NO backticks.\n" +
    "- Emojis are allowed (☀️, 📊, 🔥, 📌, ⚠️, 🔴, 🟢, 📈, 📉).\n" +
    "- Maximum 8 sentences total. Use line breaks between sections.\n" +
    "- Section 1: OVERNIGHT DELTA — major spot/price changes since yesterday's close.\n" +
    "- Section 2: INTRADAY REGIME SHIFT — any symbol that flipped positive<->negative GEX since EOD.\n" +
    "- Section 3: KEY LEVELS — call-wall or put-support migrations since yesterday's EOD.\n" +
    "- Section 4: WATCH POINTS — the most acute pressure point for today's session.\n" +
    "Be analytical, concise, and confident. No filler phrases.";

  const userPayload = JSON.stringify({
    type: "OPEN",
    as_of: new Date().toISOString(),
    intraday_now: intraResults,
    eod_yesterday: priorEodSnapshot ? {
      generatedAt: priorEodSnapshot.generatedAt,
      et_date: priorEodSnapshot.today,
      text: priorEodSnapshot.text,
      underlyingCount: priorEodSnapshot.underlyingCount,
      model: priorEodSnapshot.model,
    } : null,
  });
  return {
    messages: [
      { role: "system", content: sysPrompt },
      { role: "user", content: userPayload + "\n\nProduce the morning update now." },
    ],
  };
}

async function generateDailySummary(type, results, env, priorSnap) {
  if (!env.AI) return { text: null, model: null, latencyMs: 0, reason: "no_ai_binding" };
  const minimized = minimizeDataForAI(results);
  if (minimized.length === 0) return { text: null, model: null, latencyMs: 0, reason: "no_results" };
  const prompt = type === "OPEN"
    ? buildOpenPrompt(minimized, priorSnap)
    : buildEodPrompt(minimized);
  const startMs = Date.now();
  // Same primary-then-fallback pattern as the 15-min summary.
  try {
    const text = await callWorkersAIWithTimeout(env, AI_MODEL_PRIMARY, prompt);
    if (text && text.length >= 30) {
      return { text: sanitizeAiText(text), model: AI_MODEL_PRIMARY, latencyMs: Date.now() - startMs };
    }
    console.warn(`[AI] ${type} primary returned empty/short text (len=${text?.length})`);
  } catch (e1) {
    console.warn(`[AI] ${type} primary failed: ${e1.message}. Trying fallback.`);
  }
  try {
    const text = await callWorkersAIWithTimeout(env, AI_MODEL_FALLBACK, prompt);
    if (text && text.length >= 30) {
      return { text: sanitizeAiText(text), model: AI_MODEL_FALLBACK, latencyMs: Date.now() - startMs };
    }
  } catch (e2) {
    console.error(`[AI] ${type} both models failed: primary=${e1?.message} fallback=${e2.message}`);
    return { text: null, model: null, latencyMs: Date.now() - startMs, reason: "both_failed" };
  }
  return { text: null, model: null, latencyMs: Date.now() - startMs, reason: "empty_response" };
}

async function broadcastDailySummary(type, text, env) {
  if (!text || text.length < 30) return 0;
  const emoji = type === "EOD" ? "🌅" : "☀️";
  const label = type === "EOD" ? "EOD RECAP" : "MORNING UPDATE";
  const header = `${emoji} KRUPP CAPITAL AI — ${label}\n${"━".repeat(20)}\n`;
  const footer =
    `\n${"━".repeat(20)}\n_Krupp Capital Quantitative Desk — ${type} AI Synthesis_\n_Precision in Chaos, Alpha in Variance_`;
  const fullText = header + text + footer;

  // Same broadcast-union strategy as the 15-min summary: union across SYMBOLS + TELEGRAM_CHAT_ID.
  const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
  const union = new Set();
  for (const sym of symbols) {
    const list = await env.GEX_KV.get(`gex:subs:${sym}`, "json") || [];
    for (const cid of list) union.add(String(cid));
  }
  if (env.TELEGRAM_CHAT_ID) union.add(String(env.TELEGRAM_CHAT_ID));
  const fmtMap = {};
  await Promise.all([...union].map(async cid => { fmtMap[cid] = await getUserFormat(cid, env); }));
  const standardRecipients = [...union].filter(cid => fmtMap[cid] !== "menthorq");
  if (standardRecipients.length === 0) return 0;
  await chunkedSend(standardRecipients, cid => sendTelegramMessage(cid, fullText, env));
  return standardRecipients.length;
}

async function runDailyDigest(type, env) {
  const today = todayEtDate();
  const lastRunKey = type === "EOD" ? EOD_LAST_RUN_KEY : OPEN_LAST_RUN_KEY;
  const cacheKey  = type === "EOD" ? EOD_CACHE_KEY    : OPEN_CACHE_KEY;

  // Idempotency: skip if we already ran successfully for today's ET business day.
  // Both crons (20:30 + 21:30 UTC) over-fire on purpose to cover EDT + EST; only
  // one of them actually does work per day.
  const lastRun = await env.GEX_KV.get(lastRunKey);
  if (lastRun === today) {
    console.log(`[${type}] Already ran for ${today} (lastRun=${lastRun}). Skipping.`);
    return { ok: false, reason: "already_ran_today", today };
  }

  // Collect all 7 symbols sequentially (matches the 15-min pattern).
  // Runs inside ctx.waitUntil which has a 30s free-tier wall clock; 7 collectGEX
  // (~7s) + AI inference (~4s) + KV writes + Telegram broadcast (~2s) total ~13s.
  const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
  const results = [];
  for (const sym of symbols) {
    try {
      const r = await collectGEX(sym, env);
      results.push(r);
    } catch (e) {
      console.error(`[${type}] collectGEX ${sym} failed: ${e.message}`);
      results.push({ symbol: sym, error: e.message });
    }
  }
  const okCount = results.filter(r => !r.error).length;
  if (okCount === 0) {
    // Don't write lastRun: the second cron slot in the DST pair (e.g., 21:30 if 20:30
    // failed) will retry on a fresh API state.
    console.log(`[${type}] all ${symbols.length} collectGEX failed for ${today}; aborting before AI. Retry slot will run later.`);
    return { ok: false, reason: "all_collect_failed", today };
  }

  // OPEN summary references the most recent EOD (yesterday's close on weekdays,
  // last Friday's EOD on Monday morning) so the AI has explicit prior context.
  let priorEod = null;
  if (type === "OPEN") {
    priorEod = await env.GEX_KV.get(EOD_CACHE_KEY, "json") || null;
  }

  const summary = await generateDailySummary(type, results, env, priorEod);
  // Even on AI failure, write lastRun so we don't infinite-loop on a broken AI binding.
  // The /eod or /open chat command will report "no recent analysis" so the user sees it.
  await env.GEX_KV.put(lastRunKey, today);

  if (!summary || !summary.text) {
    console.log(`[${type}] AI summary unavailable for ${today}: ${summary?.reason || "no_text"} (${summary?.latencyMs || 0}ms)`);
    return { ok: false, reason: summary?.reason || "ai_no_text", today };
  }

  const sent = await broadcastDailySummary(type, summary.text, env);

  // Cache the result so /eod and /open chat commands read it instantly without re-running AI.
  // OPEN also stores priorEodRef so the chat command can surface "compared to EOD YYYY-MM-DD".
  const cacheRecord = {
    text: summary.text,
    model: summary.model,
    latencyMs: summary.latencyMs,
    generatedAt: new Date().toISOString(),
    type: type,
    today: today,
    recipientCount: sent,
    underlyingCount: okCount,
    priorEodRef: type === "OPEN" && priorEod ? {
      generatedAt: priorEod.generatedAt,
      today: priorEod.today,
    } : null,
  };
  await env.GEX_KV.put(cacheKey, JSON.stringify(cacheRecord));

  console.log(`[${type}] daily digest sent to ${sent} chats in ${summary.latencyMs}ms via ${summary.model} for ${today}`);
  return { ok: true, sent, model: summary.model, today };
}

// ================================================================
// USER / SUBSCRIPTION MANAGEMENT
// ================================================================

async function getUserFormat(chatId, env) {
  try {
    const u = await env.GEX_KV.get(`gex:user:${chatId}`, "json");
    return u?.format || "standard";
  } catch { return "standard"; }
}

async function setUserFormat(chatId, format, env) {
  return env.GEX_KV.put(`gex:user:${chatId}`, JSON.stringify({
    chatId: String(chatId), format, updatedAt: new Date().toISOString()
  }));
}

async function addSubscriber(chatId, symbol, env) {
  await env.GEX_KV.put(`gex:sub:${symbol}:${chatId}`, JSON.stringify({
    symbol, chatId: String(chatId), subscribedAt: new Date().toISOString(), active: true
  }));
  const idx = await env.GEX_KV.get(`gex:subs:${symbol}`, "json") || [];
  if (!idx.includes(String(chatId))) {
    idx.push(String(chatId));
    await env.GEX_KV.put(`gex:subs:${symbol}`, JSON.stringify(idx));
  }
}

async function removeSubscriber(chatId, symbol, env) {
  await env.GEX_KV.delete(`gex:sub:${symbol}:${chatId}`);
  const idx = await env.GEX_KV.get(`gex:subs:${symbol}`, "json") || [];
  await env.GEX_KV.put(`gex:subs:${symbol}`, JSON.stringify(idx.filter(id => id !== String(chatId))));
}

async function autoSubscribe(chatId, env) {
  const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
  for (const sym of symbols) await addSubscriber(chatId, sym, env);
}

// ================================================================
// TELEGRAM MESSAGE SENDER
// ================================================================

async function sendTelegramMessage(chatId, text, env) {
  const botToken = env.TELEGRAM_BOT_TOKEN;
  if (!botToken) {
    console.log("[TELEGRAM] TELEGRAM_BOT_TOKEN not set, skipping");
    return false;
  }
  if (!chatId) {
    console.log("[TELEGRAM] no chat_id, skipping");
    return false;
  }
  // Telegram max message length is 4096 chars
  const chunks = [];
  if (text.length <= 4096) {
    chunks.push(text);
  } else {
    for (let i = 0; i < text.length; i += 4000) chunks.push(text.slice(i, i + 4000));
  }

  const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
  let allOk = true;
  for (const chunk of chunks) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId, text: chunk,
          disable_web_page_preview: true,
          // parse_mode intentionally omitted — Telegram's Markdown parser
          // was rejecting cron broadcasts with "can't parse entities"
          // (incident 2026-07-16, captured in SECURITY_AUDIT.md §7.5).
          // Plain-text rendering unblocks the broadcast path now; revisit
          // Approach C (markdown-aware chunking) per followup.
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) {
        console.error(`[TELEGRAM] send failed (${res.status}): ${data.description || "unknown"}`);
        allOk = false;
      }
    } catch (e) {
      console.error(`[TELEGRAM] error: ${e.message}`);
      allOk = false;
    }
  }
  return allOk;
}

// Chunked send helper — respects Telegram rate limits (~30 msg/sec global, 1/sec per chat).
// batchSize=25, batchDelayMs=1000 → ~25 msg/sec (safe under global limit).
async function chunkedSend(recipients, sendFn, batchSize = 25, batchDelayMs = 1000) {
  for (let i = 0; i < recipients.length; i += batchSize) {
    const batch = recipients.slice(i, i + batchSize);
    const results = await Promise.allSettled(batch.map(sendFn));
    const failed = results.filter(r => r.status === "rejected" || r.value === false).length;
    if (failed > 0) console.log(`[chunkedSend] batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(recipients.length / batchSize)}: ${batch.length - failed}/${batch.length} OK`);
    if (i + batchSize < recipients.length) await new Promise(r => setTimeout(r, batchDelayMs));
  }
}

// ================================================================
// TELEGRAM COMMAND HANDLER
// ================================================================

function buildExampleMenthorQ(symbol, env) {
  // Minimal example for /format menthorq preview using the latest cached data
  return formatMenthorQGexReport({
    symbol, spot: 0, callWall: { strike: 0 }, putSupport: { strike: 0 }, hvl: 0,
    dayMin: 0, dayMax: 0, zeroDte: null, topNetGexStrikes: []
  });
}

// Strip the @<bot_username> suffix that Telegram appends in groups.
// Guards: blank text, very long text (>2000), and command-like prefix.
async function handleTelegramMessage(message, env) {
  const chatId = String(message.chat.id);
  const text = (message.text || "").trim();
  if (!text) return;
  if (text.length > 2000) {
    return sendTelegramMessage(chatId, "❌ Nachricht zu lang. Versuche /help.", env);
  }

  // Any message auto-subscribes the user to the configured symbols
  await autoSubscribe(chatId, env);

  const tokens = text.split(/\s+/);
  // Normalize: drop @bot_username suffix (Telegram appends it in groups)
  const cmd = (tokens[0] || "").replace(/@\w+$/, "").toLowerCase();
  const arg = tokens[1];

  // /start
  if (cmd === "/start") {
    const fmt = await getUserFormat(chatId, env);
    const reply = [
      `👋 *Willkommen beim GEX Worker!*\n`,
      `Deine Chat-ID: \`${chatId}\``,
      `Aktuelles Format: *${fmt === "menthorq" ? "MenthorQ (single-line CSV)" : "Standard (mehrzeilig)"}*`,
      ``,
      `Du erhältst jetzt *automatisch alle 15 Minuten* den GEX-Report.`,
      ``,
      `Befehle:`,
      `/help - Hilfe`,
      `/status [TICKER] - Aktuelle Levels abrufen`,
      `/format standard|menthorq - Format wechseln`,
      `/symbols - Unterstützte Ticker`,
      `📩 Sende einfach ein Ticker-Symbol (z.B. \`SPX\`) für sofortige Quote.`,
    ].join("\n");
    return sendTelegramMessage(chatId, reply, env);
  }

  // /help
  if (cmd === "/help") {
    const reply = [
      `📚 *GEX Worker — Hilfe*\n`,
      `📡 Daten: CBOE Delayed Quotes (verzögert ~15 Min)`,
      `🔁 Fallback: Yahoo Finance für Spot & 1D Min/Max`,
      `🧠 AI: Workers AI (Llama 3.1 8B) — Executive Summary alle 15 Min`,
      ``,
      `Befehle:`,
      `  /start — Begrüßung & Chat-ID`,
      `  /help — Diese Hilfe`,
      `  /status [TICKER] — Aktuelle Levels (Standard oder MenthorQ-Format)`,
      `  /format standard | menthorq — Format wechseln`,
      `  /symbols — Unterstützte Ticker`,
      `  /ai — Krupp Capital AI Executive Summary (15-Min)`,
      `  /eod — Letzter EOD Recap (Schluss 16:15 ET)`,
      `  /open — Morgen-Update vs. gestrigem EOD`,
      `  /subscribe TICKER — Nur bestimmtes Symbol aktivieren`,
      `  /unsubscribe TICKER — Symbol deaktivieren`,
      ``,
      `Du kannst auch einfach ein Ticker-Symbol senden (\`SPX\`, \`VIX\`, …).`,
      ``,
      `⏱️ Automatischer Broadcast: alle 15 Minuten`,
    ].join("\n");
    return sendTelegramMessage(chatId, reply, env);
  }

  // /ai — fetch the cached executive summary from the most recent cron tick.
  // Zero AI cost on this path because we never re-run inference; we just read KV.
  if (cmd === "/ai") {
    const cached = await env.GEX_KV.get(AI_CACHE_KEY, "json");
    if (!cached || !cached.text || cached.text.length < 30) {
      return sendTelegramMessage(chatId,
        `🧠 *AI Executive Summary*\n\nNoch keine Analyse verfügbar — der nächste Cron-Tick läuft in ≤15 Min.\n\nTipp: warte oder setze \`warmup=true\` per Admin.`,
        env);
    }
    const ageMs = Date.now() - new Date(cached.generatedAt).getTime();
    const ageMin = Math.max(0, Math.round(ageMs / 60000));
    const staleTag = ageMin > 45 ? `\n⚠️ Hinweis: Analyse ist ${ageMin} Min alt.\n` : "";
    const metaTag = `\n[Model: ${cached.model} | ${ageMin} Min alt]`;
    return sendTelegramMessage(chatId, `🧠 *AI Summary*\n\n${cached.text}${staleTag}${metaTag}`, env);
  }

  // /eod — read the most recent EOD digest from KV (zero AI cost).
  if (cmd === "/eod") {
    const cached = await env.GEX_KV.get(EOD_CACHE_KEY, "json");
    if (!cached || !cached.text) {
      return sendTelegramMessage(chatId,
        `🌅 *EOD Recap*\n\nNoch kein EOD gespeichert — der nächste EOD-Lauf ist um 16:15 ET (≈20:30/21:30 UTC).`,
        env);
    }
    const ageHrs = Math.max(0, Math.round((Date.now() - new Date(cached.generatedAt).getTime()) / 3600000));
    const staleTag = ageHrs > 18 ? `\n⚠️ Hinweis: EOD ist ${ageHrs}h alt (ggf. Wochenende / Markttag ohne Daten).\n` : "";
    return sendTelegramMessage(chatId, `🌅 *EOD Recap (${cached.today})*\n\n${cached.text}${staleTag}\n[Model: ${cached.model} | ${ageHrs}h alt | ${cached.underlyingCount}/${cached.underlyingCount} Symbole OK]`, env);
  }

  // /open — read the most recent OPEN digest (zero AI cost). Surfaces priorEodRef when
  // the OPEN was generated against a prior-day EOD, so the user knows the comparison anchor.
  if (cmd === "/open") {
    const cached = await env.GEX_KV.get(OPEN_CACHE_KEY, "json");
    if (!cached || !cached.text) {
      return sendTelegramMessage(chatId,
        `☀️ *Morning Update*\n\nNoch kein OPEN gespeichert — der nächste OPEN-Lauf ist um 9:45 ET (≈13:45/14:45 UTC).`,
        env);
    }
    const ageHrs = Math.max(0, Math.round((Date.now() - new Date(cached.generatedAt).getTime()) / 3600000));
    const staleTag = ageHrs > 18 ? `\n⚠️ Hinweis: OPEN ist ${ageHrs}h alt.\n` : "";
    const refTag = cached.priorEodRef
      ? `\n[Vergleich zu EOD ${cached.priorEodRef.today} | Model: ${cached.model} | ${ageHrs}h alt]`
      : `\n[Model: ${cached.model} | ${ageHrs}h alt | kein EOD-Vergleich verfügbar]`;
    return sendTelegramMessage(chatId, `☀️ *Morning Update (${cached.today})*\n\n${cached.text}${staleTag}${refTag}`, env);
  }

  // /format standard|menthorq
  if (cmd === "/format") {
    const newFormat = (arg || "").toLowerCase();
    if (newFormat !== "standard" && newFormat !== "menthorq") {
      return sendTelegramMessage(chatId, `❌ Bitte \`/format standard\` oder \`/format menthorq\``, env);
    }
    await setUserFormat(chatId, newFormat, env);
    let preview;
    if (newFormat === "menthorq") {
      preview = [
        `✅ *Format: MenthorQ (single-line CSV)*\n`,
        `Beispiel ($SPX):`,
        `\`$SPX: Call Resistance, 7500, Put Support, 7400, HVL, 7450, 1D Min, 7380.50, 1D Max, 7560.20, Call Resistance 0DTE, 7525, Put Support 0DTE, 7475, HVL 0DTE, 7500, Gamma Wall 0DTE, 7525, GEX 1, 7500, GEX 2, 7475, ...\``,
      ].join("\n");
    } else {
      preview = [
        `✅ *Format: Standard (mehrzeilig)*\n`,
        `Beispiel:`,
        `\`\`\``,
        `*SPX* | GEX Report`,
        `━━━━━━━━━━━━━━━━━━━━━`,
        `💰 Spot: 7515.34 (-0.80%)`,
        `📈 Net GEX: -16.6M`,
        `🔵 Call Wall: 7525`,
        `🔴 Put Support: 7475`,
        `\`\`\``,
      ].join("\n");
    }
    return sendTelegramMessage(chatId, preview, env);
  }

  // /status [TICKER]
  if (cmd === "/status") {
    const symbolsArg = arg ? [arg.toUpperCase()] : (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
    return sendStatusToChat(chatId, symbolsArg, env);
  }

  // /symbols
  if (cmd === "/symbols") {
    const list = Object.keys(SYMBOL_CONFIG).join(", ");
    return sendTelegramMessage(chatId, `📊 *Unterstützte Symbole:*\n\n${list}\n\n_Indizes (mit \`^\`-Prefix via CBOE): ${INDEX_SYMBOLS.join(", ")}_`, env);
  }

  // /subscribe TICKER / /unsubscribe TICKER
  if (cmd === "/subscribe") {
    const sym = (arg || "").toUpperCase();
    if (!sym || !SYMBOL_CONFIG[sym]) {
      return sendTelegramMessage(chatId, `❌ Unbekanntes Symbol. Liste: ${Object.keys(SYMBOL_CONFIG).join(", ")}`, env);
    }
    await addSubscriber(chatId, sym, env);
    return sendTelegramMessage(chatId, `✅ *Subscribed* zu *${sym}*`, env);
  }
  if (cmd === "/unsubscribe") {
    const sym = (arg || "").toUpperCase();
    if (!sym || !SYMBOL_CONFIG[sym]) {
      return sendTelegramMessage(chatId, `❌ Unbekanntes Symbol. Liste: ${Object.keys(SYMBOL_CONFIG).join(", ")}`, env);
    }
    await removeSubscriber(chatId, sym, env);
    return sendTelegramMessage(chatId, `✅ *Unsubscribed* von *${sym}*`, env);
  }

  // /summary (admin telegram command) — returns the cached summary instantly. Same
  // behavior as /ai; aliased so automation tools that trigger by /summary still work.
  if (cmd === "/summary") {
    return handleTelegramMessage({ ...message, text: "/ai" }, env);
  }

  // Plain text → could be a TICKER
  const ticker = text.replace(/^\$/, "").toUpperCase().split(/\s+/)[0];
  if (SYMBOL_CONFIG[ticker]) {
    try {
      const result = await collectGEX(ticker, env);
      const fmt = await getUserFormat(chatId, env);
      const msg = fmt === "menthorq" ? formatMenthorQGexReport(result) : formatTelegramGexReport(result);
      return sendTelegramMessage(chatId, msg, env);
    } catch (e) {
      return sendTelegramMessage(chatId, `❌ Fehler bei ${ticker}: ${e.message}`, env);
    }
  }

  // Unknown → friendly default
  return sendTelegramMessage(chatId,
    `🤔 Ich verstehe deine Nachricht nicht. Versuche /help oder sende einen Ticker (z.B. \`SPX\`, \`VIX\`).`,
    env);
}

async function sendStatusToChat(chatId, symbols, env) {
  const fmt = await getUserFormat(chatId, env);
  for (const sym of symbols) {
    if (!SYMBOL_CONFIG[sym]) {
      await sendTelegramMessage(chatId, `❌ Unbekanntes Symbol: ${sym}`, env);
      continue;
    }
    try {
      const result = await collectGEX(sym, env);
      const msg = fmt === "menthorq" ? formatMenthorQGexReport(result) : formatTelegramGexReport(result);
      await sendTelegramMessage(chatId, msg, env);
    } catch (e) {
      await sendTelegramMessage(chatId, `❌ Fehler bei ${sym}: ${e.message}`, env);
    }
  }
}

// ================================================================
// TELEGRAM WEBHOOK ROUTES
// ================================================================

async function handleTelegramWebhook(request, env) {
  // Secret-token verification (Telegram sends X-Telegram-Bot-Api-Secret-Token when configured).
  // If WEBHOOK_SECRET_TOKEN is unset, we log a loud warning but still accept (back-compat).
  if (env.WEBHOOK_SECRET_TOKEN) {
    const got = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (got !== env.WEBHOOK_SECRET_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }
  } else {
    console.warn("[WEBHOOK] WEBHOOK_SECRET_TOKEN unset — webhook is OPEN. Set via wrangler secret put WEBHOOK_SECRET_TOKEN to lock down.");
  }
  let update;
  try {
    update = await request.json();
  } catch {
    return new Response("bad json", { status: 400 });
  }
  if (update?.message) {
    try {
      await handleTelegramMessage(update.message, env);
    } catch (e) {
      console.error(`[WEBHOOK] handler error: ${e.message}`);
    }
  }
  return new Response("ok", { status: 200 });
}

async function setupTelegramWebhook(env, targetUrl, secret) {
  const botToken = env.TELEGRAM_BOT_TOKEN;
  if (!botToken) return { ok: false, error: "TELEGRAM_BOT_TOKEN not set" };
  const url = `https://api.telegram.org/bot${botToken}/setWebhook`;
  const body = { url: targetUrl, allowed_updates: ["message"], drop_pending_updates: true };
  if (secret) body.secret_token = secret;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return await res.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function clearTelegramWebhook(env) {
  const botToken = env.TELEGRAM_BOT_TOKEN;
  if (!botToken) return { ok: false, error: "TELEGRAM_BOT_TOKEN not set" };
  try {
    const res = await fetch(`https://api.telegram.org/bot${botToken}/deleteWebhook?drop_pending_updates=true`);
    return await res.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function getTelegramWebhookInfo(env) {
  const botToken = env.TELEGRAM_BOT_TOKEN;
  if (!botToken) return { ok: false, error: "TELEGRAM_BOT_TOKEN not set" };
  try {
    const res = await fetch(`https://api.telegram.org/bot${botToken}/getWebhookInfo`);
    return await res.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// ================================================================
// COLLECT GEX (now with 0DTE block + 1D Min/Max)
// ================================================================

async function collectGEX(symbol, env) {
  const cfg = SYMBOL_CONFIG[symbol];
  if (!cfg) throw new Error(`UNKNOWN_SYMBOL: ${symbol}`);

  const spot = await fetchSpot(symbol);
  const chain = await fetchChain(symbol, spot.price, spot.iv30);

  const gex = computeGEX(chain.frontStrikes, spot.price);

  // 0DTE block (if same-day expiry exists)
  let zeroDte = null;
  if (chain.zeroDteStrikes && chain.zeroDteStrikes.length > 0) {
    const z = computeGEX(chain.zeroDteStrikes, spot.price);
    zeroDte = {
      expiry: chain.zeroDteExpiry,
      dte: 0,
      callResistance: z.callWall.strike,
      putSupport: z.putSupport.strike,
      hvl: z.hvl,
      gammaWall: z.callWall.strike, // gamma wall = top call resistance at 0DTE
      netGex: Math.round(z.netGEX * 100) / 100,
    };
  }

  // 1D Min/Max via Yahoo intraday (best-effort, may be null)
  const intraday = await fetchIntradayRange(symbol);

  const prevRaw = await env.GEX_KV.get(`gex:${symbol}:previous`, "json");
  const regimeChange = detectRegimeChange(prevRaw, gex);

  const result = {
    timestamp: new Date().toISOString(),
    symbol, spot: spot.price, spotSource: spot.source, label: cfg.label,
    iv30: spot.iv30 || null,
    spotChangePct: spot.changePct || null,
    regime: gex.regime,
    regimeChanged: regimeChange.changed,
    regimeChangeReason: regimeChange.changed
      ? `${regimeChange.prevRegime} -> ${regimeChange.currRegime} | ${regimeChange.gexDeltaPercent}%`
      : null,
    netGex: Math.round(gex.netGEX * 100) / 100,
    netGexFormatted: formatGex(gex.netGEX),
    netGexNormalized: normalizeGEX(gex.netGEX),
    callWall: {
      strike: gex.callWall.strike,
      gex: formatGex(gex.callWall.gex),
      distance: Math.round((gex.callWall.strike - spot.price) / spot.price * 10000) / 100
    },
    putSupport: {
      strike: gex.putSupport.strike,
      gex: formatGex(gex.putSupport.gex),
      distance: Math.round((gex.putSupport.strike - spot.price) / spot.price * 10000) / 100
    },
    hvl: gex.hvl,
    topCallStrikes: gex.topCalls.map(s => ({ strike: s.strike, oi: s.oi, gex: formatGex(s.gex) })),
    topPutStrikes: gex.topPuts.map(s => ({ strike: s.strike, oi: s.oi, gex: formatGex(s.gex) })),
    topNetGexStrikes: gex.topNetGex.map(s => s.strike),
    dayMin: intraday?.min ?? null,
    dayMax: intraday?.max ?? null,
    zeroDte,
    chainSource: chain.source,
    frontExpiry: chain.frontExpiry,
    dte: chain.dte,
    strikeCount: gex.strikeCount,
    totalOptions: chain.totalOptions,
    expiriesCount: (chain.allExpiries || []).length,
    allExpiries: chain.allExpiries || [],
    fetchedAt: chain.fetchedAt,
  };

  const currentRaw = await env.GEX_KV.get(`gex:${symbol}:latest`, "json");
  if (currentRaw) await env.GEX_KV.put(`gex:${symbol}:previous`, JSON.stringify(currentRaw));
  await env.GEX_KV.put(`gex:${symbol}:latest`, JSON.stringify(result));

  if (regimeChange.changed) {
    const alert = {
      timestamp: result.timestamp, symbol, type: "REGIME_CHANGE",
      from: regimeChange.prevRegime, to: regimeChange.currRegime,
      netGex: result.netGexFormatted,
      callWall: result.callWall.strike, putSupport: result.putSupport.strike
    };
    const alertsRaw = await env.GEX_KV.get("gex:alerts", "json") || [];
    alertsRaw.unshift(alert);
    await env.GEX_KV.put("gex:alerts", JSON.stringify(alertsRaw.slice(0, 20)));
  }

  return result;
}

// ================================================================
// BROADCAST (per-user format preference)
// ================================================================

async function broadcastGexReport(symbol, result, env) {
  const subs = await env.GEX_KV.get(`gex:subs:${symbol}`, "json") || [];
  const defaultChatId = env.TELEGRAM_CHAT_ID;
  const recipients = [...new Set([...(subs || []).map(String), ...(defaultChatId ? [String(defaultChatId)] : [])])];

  if (recipients.length === 0) {
    console.log(`[TELEGRAM] no recipients for ${symbol}`);
    return;
  }

  const queue = await env.GEX_KV.get("gex:push-queue", "json") || [];
  queue.unshift({
    type: "GEX_REPORT", symbol,
    regime: result.regime, netGex: result.netGexFormatted,
    callWall: result.callWall.strike, putSupport: result.putSupport.strike,
    hvl: result.hvl, spot: result.spot, timestamp: result.timestamp,
    recipientCount: recipients.length,
  });
  await env.GEX_KV.put("gex:push-queue", JSON.stringify(queue.slice(0, 50)));

  // Batch-fetch ALL format preferences in parallel (1 KV round-trip vs N).
  const fmtMap = {};
  await Promise.all(recipients.map(async cid => {
    fmtMap[cid] = await getUserFormat(cid, env);
  }));
  const standardRecipients = recipients.filter(cid => fmtMap[cid] !== "menthorq");
  const menthorqRecipients = recipients.filter(cid => fmtMap[cid] === "menthorq");

  // Pre-format messages once per group (saves N formatter calls).
  const standardText = standardRecipients.length > 0 ? formatTelegramGexReport(result) : null;
  const menthorqText = menthorqRecipients.length > 0 ? formatMenthorQGexReport(result) : null;

  if (standardRecipients.length > 0) {
    await chunkedSend(standardRecipients, cid => sendTelegramMessage(cid, standardText, env));
  }
  if (menthorqRecipients.length > 0) {
    await chunkedSend(menthorqRecipients, cid => sendTelegramMessage(cid, menthorqText, env));
  }

  if (env.HF_WEBHOOK_URL) {
    try {
      await fetch(env.HF_WEBHOOK_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "GEX_REPORT", symbol, gex: result,
          recipientCount: recipients.length,
          formats: { standard: standardRecipients.length, menthorq: menthorqRecipients.length }
        })
      });
    } catch { /* ignore */ }
  }
}

async function broadcastRegimeChange(symbol, result, env) {
  const subs = await env.GEX_KV.get(`gex:subs:${symbol}`, "json") || [];
  const defaultChatId = env.TELEGRAM_CHAT_ID;
  const recipients = new Set([...(subs || []).map(String), ...(defaultChatId ? [String(defaultChatId)] : [])]);

  // Regime change used standard format (alerts benefit from rich layout)
  const alertText = [
    `🚨 *GEX REGIME CHANGE — ${symbol}*`,
    ``,
    `Regime: *${result.regime}*`,
    `Net GEX: *${result.netGexFormatted}*`,
    `Call Wall: *${result.callWall.strike}*`,
    `Put Support: *${result.putSupport.strike}*`,
    `HVL: *${result.hvl}*`,
    `Spot: *${result.spot.toFixed(2)}*`,
    `Time: ${result.timestamp}`,
  ].join("\n");

  if (recipients.length > 0) {
    await chunkedSend(recipients, cid => sendTelegramMessage(cid, alertText, env));
  }

  const message = {
    type: "GEX_REGIME_CHANGE", symbol, regime: result.regime,
    netGex: result.netGexFormatted, callWall: result.callWall.strike,
    putSupport: result.putSupport.strike, hvl: result.hvl,
    spot: result.spot, timestamp: result.timestamp, subscribers: Array.from(recipients)
  };
  const queue = await env.GEX_KV.get("gex:push-queue", "json") || [];
  queue.unshift(message);
  await env.GEX_KV.put("gex:push-queue", JSON.stringify(queue.slice(0, 50)));

  if (env.HF_WEBHOOK_URL) {
    try {
      await fetch(env.HF_WEBHOOK_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message)
      });
    } catch { /* ignore */ }
  }
}

// ================================================================
// WORKER ENTRY
// ================================================================

export default {
  async scheduled(event, env, ctx) {
    // Branch on the exact cron string that fired. Cloudflare sets event.cron to the
    // literal pattern from wrangler.toml — so the EOD/OPEN patterns route cleanly.
    const cronName = event?.cron || "";
    if (cronName === EOD_CRON_PATTERN)  { ctx.waitUntil(runDailyDigest("EOD", env));  return; }
    if (cronName === OPEN_CRON_PATTERN) { ctx.waitUntil(runDailyDigest("OPEN", env)); return; }

    ctx.waitUntil((async () => {
      const start = performance.now();
      try {
        const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());

        // v4.3 BROADCAST ORDER (per user request):
        //   [1] Executive Summary (AI synthesis, all 7 symbols combined)  ← FIRST
        //   [2] SPX
        //   [3] SPY
        //   [4] QQQ
        //   [5] VIX
        //   [6] GLD
        //   [7] SLV
        //   [8] USO
        // Every message ends with: Krupp Capital Quantitative Desk / Precision in Chaos, Alpha in Variance.

        // Step 1: Collect all 7 underlyings (no broadcasts yet) so the AI sees the full
        // snapshot for the cross-symbol analysis.
        const results = [];
        for (const sym of symbols) {
          try {
            const result = await collectGEX(sym, env);
            results.push(result);
          } catch (e) {
            console.error(`[GEX] ${sym} failed: ${e.message}`);
            results.push({ symbol: sym, error: e.message });
            if (env.TELEGRAM_CHAT_ID) {
              await sendTelegramMessage(env.TELEGRAM_CHAT_ID,
                `⚠️ *GEX Cron Error — ${sym}*\n\n\`${e.message}\`\n\n_Krupp Capital Quantitative Desk_\n_Precision in Chaos, Alpha in Variance_`, env);
            }
          }
        }

        // Step 2: AI executive summary fires FIRST (per requested order).
        // Wrapped in its own try/catch so a failed AI call never blocks the per-symbol reports.
        if (env.AI) {
          try {
            const summary = await generateExecutiveSummary(results, env);
            if (summary.text) {
              const sent = await broadcastExecutiveSummary(summary.text, env);
              console.log(`[AI] summary sent to ${sent} chats in ${summary.latencyMs}ms via ${summary.model}`);
              await env.GEX_KV.put(AI_CACHE_KEY, JSON.stringify({
                text: summary.text,
                generatedAt: new Date().toISOString(),
                model: summary.model,
                latencyMs: summary.latencyMs,
                recipientCount: sent,
                underlyingCount: results.filter(r => !r.error).length,
              }));
            } else {
              console.log(`[AI] summary skipped: ${summary.reason || "no_text"} (${summary.latencyMs}ms)`);
            }
          } catch (aiErr) {
            console.error(`[AI] summary pipeline error (non-fatal): ${aiErr.message}`);
          }
        }

        // Step 3: Per-symbol broadcasts in env.SYMBOLS order (SPX, SPY, QQQ, VIX, GLD, SLV, USO).
        // Symbols that failed in Step 1 are skipped here — the error notification was already sent.
        for (const sym of symbols) {
          const r = results.find(x => x && x.symbol === sym);
          if (!r || r.error) continue;
          try {
            await broadcastGexReport(sym, r, env);
            if (r.regimeChanged) await broadcastRegimeChange(sym, r, env);
          } catch (e) {
            console.error(`[GEX] broadcast ${sym} failed: ${e.message}`);
          }
        }

        const duration = (performance.now() - start).toFixed(1);
        console.log(`[GEX] Cron done in ${duration}ms: ${results.filter(r => !r.error).length}/${symbols.length} OK`);
      } catch (e) {
        console.error(`[GEX] Cron fatal: ${e.message}`);
        if (env.TELEGRAM_CHAT_ID) {
          await sendTelegramMessage(env.TELEGRAM_CHAT_ID,
            `🚨 *GEX Cron Fatal Error*\n\n\`${e.message}\``, env);
        }
      }
    })());
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // --- Telegram webhook (called by Telegram) ---
    // AWAIT synchronously: ctx.waitUntil does not reliably complete KV writes
    // on the Workers Free plan (worker dies before writes commit). Telegram
    // expects ack within <30s, our handler finishes in <2s.
    if (path === "/telegram-webhook" && request.method === "POST") {
      return await handleTelegramWebhook(request, env);
    }

    // --- Admin: register webhook at Telegram ---
    if (path === "/setup-webhook") {
      const targetUrl = url.searchParams.get("url")
        || `${url.protocol}//${url.host}/telegram-webhook`;
      const secret = url.searchParams.get("secret") || env.WEBHOOK_SECRET_TOKEN || null;
      const result = await setupTelegramWebhook(env, targetUrl, secret);
      return json(result);
    }
    if (path === "/webhook-info") {
      return json(await getTelegramWebhookInfo(env));
    }
    if (path === "/clear-webhook") {
      return json(await clearTelegramWebhook(env));
    }

    // --- Health ---
    if (path === "/health") {
      return json({ status: "ok", worker: "gex-collector", version: "4.0", ts: new Date().toISOString() });
    }

    // --- Status ---
    if (path === "/status") {
      const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
      const latest = {};
      for (const sym of symbols) {
        const data = await env.GEX_KV.get(`gex:${sym}:latest`, "json");
        latest[sym] = data ? {
          timestamp: data.timestamp,
          spot: data.spot, regime: data.regime,
          netGex: data.netGexFormatted, chainSource: data.chainSource, dte: data.dte
        } : null;
      }
      return json({
        worker: "gex-collector", version: "4.0", cron: "*/15 * * * *",
        symbols, latest, ts: new Date().toISOString()
      });
    }

    // --- Latest / Previous / Compare ---
    if (path === "/latest") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const data = await env.GEX_KV.get(`gex:${sym}:latest`, "json");
      if (!data) return json({ error: "no data", symbol: sym }, 404);
      return json(data);
    }
    if (path === "/previous") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const data = await env.GEX_KV.get(`gex:${sym}:previous`, "json");
      if (!data) return json({ error: "no previous data" }, 404);
      return json(data);
    }
    if (path === "/compare") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const [curr, prev] = await Promise.all([
        env.GEX_KV.get(`gex:${sym}:latest`, "json"),
        env.GEX_KV.get(`gex:${sym}:previous`, "json")
      ]);
      if (!curr) return json({ error: "no current data" }, 404);
      return json({ symbol: sym, comparison: detectRegimeChange(prev, curr) });
    }

    // --- Manual trigger (GET or POST) ---
    if (path === "/trigger") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const result = await collectGEX(sym, env);
      return json(result);
    }

    // --- Symbols summary ---
    if (path === "/symbols") {
      const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
      const result = {};
      for (const sym of symbols) {
        const data = await env.GEX_KV.get(`gex:${sym}:latest`, "json");
        result[sym] = data ? {
          regime: data.regime, spot: data.spot, netGex: data.netGexFormatted,
          callWall: data.callWall?.strike, putSupport: data.putSupport?.strike,
          chainSource: data.chainSource, frontExpiry: data.frontExpiry,
          zeroDte: data.zeroDte || null
        } : null;
      }
      return json({ symbols: result });
    }

    // --- Alerts queue ---
    if (path === "/alerts") {
      const alerts = await env.GEX_KV.get("gex:alerts", "json") || [];
      return json({ alerts, count: alerts.length });
    }

    // --- Subscriptions ---
    if (path === "/subscribe" && request.method === "POST") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const chatId = url.searchParams.get("chat_id") || "unknown";
      await addSubscriber(chatId, sym, env);
      await sendTelegramMessage(chatId, `✅ Subscribed to *${sym}* GEX updates.`, env);
      return json({ ok: true, action: "subscribed", symbol: sym, chatId });
    }
    if (path === "/unsubscribe" && request.method === "POST") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const chatId = url.searchParams.get("chat_id") || "unknown";
      await removeSubscriber(chatId, sym, env);
      return json({ ok: true, action: "unsubscribed" });
    }
    if (path === "/subscriptions") {
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      return json({ symbol: sym, subscribers: await env.GEX_KV.get(`gex:subs:${sym}`, "json") || [] });
    }
    if (path === "/broadcast-test") {
      // Admin: send a test GEX report to TELEGRAM_CHAT_ID in user's format or forced menthorq
      const sym = (url.searchParams.get("symbol") || "SPX").toUpperCase();
      const fmt = url.searchParams.get("format") || "standard";
      const result = await collectGEX(sym, env);
      const text = fmt === "menthorq" ? formatMenthorQGexReport(result) : formatTelegramGexReport(result);
      await sendTelegramMessage(env.TELEGRAM_CHAT_ID, text, env);
      return json({ ok: true, sentTo: env.TELEGRAM_CHAT_ID, format: fmt, len: text.length });
    }

    // --- Executive summary (Workers AI) ---
    // GET  /summary            → cached KV value only (cheap)
    // GET  /summary?status=1   → status/metadata only (text omitted, even cheaper)
    // POST /summary?force=1    → bypass cache, run AI now, write to KV (admin/costly)
    // POST /summary?send=1     → force-run AND broadcast to Telegram
    if (path === "/summary") {
      const statusOnly = url.searchParams.get("status") === "1";
      if (request.method === "POST" && (url.searchParams.get("force") === "1" || url.searchParams.get("send") === "1")) {
        try {
          const symbols = (env.SYMBOLS || "SPX,VIX").split(",").map(s => s.trim());
          const results = [];
          for (const sym of symbols) {
            try { results.push(await collectGEX(sym, env)); }
            catch (e) { results.push({ symbol: sym, error: e.message }); }
          }
          const summary = await generateExecutiveSummary(results, env);
          let sent = 0;
          if (summary.text) {
            await env.GEX_KV.put(AI_CACHE_KEY, JSON.stringify({
              text: summary.text,
              generatedAt: new Date().toISOString(),
              model: summary.model,
              latencyMs: summary.latencyMs,
            }));
            if (url.searchParams.get("send") === "1") sent = await broadcastExecutiveSummary(summary.text, env);
          }
          return json({
            ok: !!summary.text,
            mode: url.searchParams.get("send") === "1" ? "force_send" : "force_cache",
            model: summary.model,
            latencyMs: summary.latencyMs,
            text: statusOnly ? undefined : summary.text,
            sentTo: sent,
            reason: summary.reason || null,
          });
        } catch (e) { return json({ error: e.message }, 500); }
      }
      const cached = await env.GEX_KV.get(AI_CACHE_KEY, "json");
      if (!cached) return json({
        ok: false,
        error: "no summary cached yet — wait for next cron tick, or POST /summary?force=1",
      }, 404);
      const ageMin = Math.max(0, Math.round((Date.now() - new Date(cached.generatedAt).getTime()) / 60000));
      return json({
        ok: true,
        cached: true,
        model: cached.model,
        generatedAt: cached.generatedAt,
        ageMin,
        recipientCount: cached.recipientCount ?? null,
        text: statusOnly ? undefined : cached.text,
      });
    }

    // --- TradingView webhook bridge ---
    if (path === "/webhook" && request.method === "POST") {
      try {
        const body = await request.json();
        // Map the incoming ticker directly to its own chain. No proxy mapping — SPY/QQQ/IWM
        // have their own options chains on CBOE and should report on their own data, not
        // on tracks of the underlying index.
        const sym = (body.symbol || body.ticker || "SPX").toUpperCase();
        const result = await collectGEX(sym, env);
        if (env.HF_WEBHOOK_URL) {
          await fetch(env.HF_WEBHOOK_URL, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: "tv-webhook", symbol: sym, gex: result })
          });
        }
        return json({ ok: true, symbol: sym, regime: result.regime });
      } catch (e) { return json({ error: e.message }, 400); }
    }

    // --- Default ---
    return json({
      worker: "gex-collector", version: "4.0",
      features: [
        "Open Telegram bot (webhook) — anyone can message",
        "Auto-subscribe on first message",
        "Two formats: standard (default) or MenthorQ single-line CSV",
        "Per-user format preference via /format command",
        "0DTE separate GEX block + 1D Min/Max + Top-10 GEX strikes",
        "🧠 Workers AI — Krupp Capital Quantitative Analyst AI executive summary every 15 min",
        "🌅☀️ Daily EOD + OPEN digests (weekdays, post-16:15 ET & 9:45 ET)",
      ],
      endpoints: {
        health: ["/health", "/status", "/latest?symbol=SPX", "/previous", "/compare", "/symbols", "/trigger", "/alerts", "/summary"],
        subscriptions: ["/subscribe (POST)?symbol=&chat_id=", "/unsubscribe (POST)?symbol=&chat_id=", "/subscriptions?symbol="],
        telegram: ["/telegram-webhook (POST)", "/setup-webhook", "/webhook-info", "/clear-webhook", "/broadcast-test?format=menthorq"],
        bridge: ["/webhook (POST)"],
      },
      bot_commands: ["/start", "/help", "/status", "/format standard|menthorq", "/symbols", "/ai", "/eod", "/open", "/subscribe TICKER", "/unsubscribe TICKER"],
      cron: "*/15 * * * *"
    });
  }
};

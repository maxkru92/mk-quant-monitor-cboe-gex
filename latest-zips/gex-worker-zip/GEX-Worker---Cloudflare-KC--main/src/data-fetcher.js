// ================================================
// DATA FETCHER (CBOE Primary, Yahoo Fallback)
// ================================================

const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const SYMBOL_CONFIG = {
  SPX:  { proxy: "SPY", spot: "^SPX", label: "S&P 500",      contractSize: 100 },
  VIX:  { proxy: "VIXY", spot: "^VIX", label: "VIX",           contractSize: 100 },
  NDX:  { proxy: "QQQ",  spot: "^NDX", label: "Nasdaq 100",  contractSize: 100 },
  RUT:  { proxy: "IWM",  spot: "^RUT", label: "Russell 2000", contractSize: 100 },
  SPY:  { proxy: "SPY",  spot: "SPY",  label: "SPY ETF",       contractSize: 100 },
  QQQ:  { proxy: "QQQ",  spot: "QQQ",  label: "QQQ ETF",       contractSize: 100 },
  IWM:  { proxy: "IWM",  spot: "IWM",  label: "IWM ETF",       contractSize: 100 },
};

// ================================================
// CBOE SPOT FETCH (Primary)
// ================================================

async function fetchSpotFromCBOE(symbol, env) {
  try {
    const prefix = ["SPX", "VIX", "NDX", "RUT", "OEX", "XEO", "SPXW"].includes(symbol.toUpperCase()) ? "^" : "";
    const url = `https://www.cboe.com/education/tools/trade-optimizer/symbol-info/?symbol=${prefix}${symbol}`;
    
    const res = await fetch(url, {
      headers: { "User-Agent": UA, "Accept": "application/json" },
      cf: { cacheTtl: 300 }
    });
    
    if (!res.ok) return null;
    const data = await res.json();
    
    if (!data?.success || !data?.details) return null;
    
    const price = parseFloat(data.details.current_price);
    if (!price || price <= 0) return null;
    
    const change = parseFloat(data.details.price_change || 0);
    const changePct = parseFloat(data.details.price_change_percent || 0);
    const iv30 = parseFloat(data.details.iv30 || 0);
    
    return {
      price, change, changePct, iv30,
      source: "cboe", symbol, fetchedAt: new Date().toISOString()
    };
  } catch (e) {
    console.log(`[CBOE FAIL] ${symbol}: ${e.message}`);
    return null;
  }
}

// ================================================
// YAHOO FINANCE SPOT (Fallback)
// ================================================

async function fetchSpotFromYahoo(symbol, env) {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=15m&range=1d`;
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    
    if (!res.ok) return null;
    const data = await res.json();
    
    const closes = data?.chart?.result?.[0]?.indicators?.quote?.[0]?.close;
    if (!closes || closes.length === 0) return null;
    
    let lastValid = null;
    for (let i = closes.length - 1; i >= 0; i--) {
      if (closes[i] !== null && closes[i] > 0) {
        lastValid = closes[i];
        break;
      }
    }
    
    if (!lastValid) return null;
    
    return {
      price: lastValid,
      source: "yahoo", symbol, fetchedAt: new Date().toISOString()
    };
  } catch (e) {
    console.log(`[YAHOO FAIL] ${symbol}: ${e.message}`);
    return null;
  }
}

// ================================================
// SPOT FETCH PRIORITY (CBOE → Yahoo)
// ================================================

async function fetchSpot(symbol, env) {
  // Try CBOE first
  let spot = await fetchSpotFromCBOE(symbol, env);
  
  if (spot) return spot;
  
  // Fallback to Yahoo
  spot = await fetchSpotFromYahoo(symbol, env);
  
  if (spot) return spot;
  throw new Error(`FETCH_ERROR: no spot for ${symbol} (CBOE + Yahoo failed)`);
}

// ================================================
// CBOE WITH IV30 (SPX only, primary)
// ================================================

async function fetchSpotWithIV30(symbol, env) {
  const primary = ["SPX", "NDX", "RUT", "OEX", "XEO"];
  if (!primary.includes(symbol.toUpperCase())) return null;
  
  return await fetchSpotFromCBOE(symbol, env);
}

// ================================================
// OPTIONS CHAIN FETCH (CBOE → Yahoo Fallback)
// ================================================

async function fetchOptionsChain(underlying, env) {
  // CBOE Blockiert direkt, Yahoo ist Primary
  // Yahoo ist zuverlässiger für Options Chains
  return await fetchOptionsChainFromYahoo(underlying, env);
}

async function fetchOptionsChainFromYahoo(underlying, env) {
  try {
    const url = `https://query1.finance.yahoo.com/v7/finance/options/${encodeURIComponent(underlying)}`;
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    
    if (!res.ok) return null;
    const data = await res.json();
    
    const result = data?.optionChain?.result?.[0];
    if (!result) return null;
    
    const underlyingPrice = result.quote?.regularMarketPrice ?? null;
    const expiry = result.expirationDates?.[0];
    if (!expiry) return null;
    
    const strikes = result.options?.[0];
    if (!strikes) return null;
    
    // Map calls and puts
    const callMap = new Map();
    const putMap = new Map();
    
    for (const opt of strikes.calls || []) {
      callMap.set(opt.strike, {
        strike: opt.strike,
        oi: opt.openInterest ?? 0,
        iv: opt.impliedVolatility ?? 0,
        gamma: opt.gamma ?? 0,
        delta: opt.delta ?? 0,
        bid: opt.bid ?? 0,
        ask: opt.ask ?? 0
      });
    }
    
    for (const opt of strikes.puts || []) {
      putMap.set(opt.strike, {
        strike: opt.strike,
        oi: opt.openInterest ?? 0,
        iv: opt.impliedVolatility ?? 0,
        gamma: opt.gamma ?? 0,
        delta: opt.delta ?? 0,
        bid: opt.bid ?? 0,
        ask: opt.ask ?? 0
      });
    }
    
    // Combine
    const allStrikes = new Set([...callMap.keys(), ...putMap.keys()]);
    const combined = [];
    
    for (const strike of allStrikes) {
      const c = callMap.get(strike), p = putMap.get(strike);
      combined.push({
        strike,
        callOI: c?.oi ?? 0,
        putOI: p?.oi ?? 0,
        callGamma: c?.gamma ?? 0,
        putGamma: p?.gamma ?? 0,
        callIV: c?.iv ?? 0,
        putIV: p?.iv ?? 0
      });
    }
    
    // Filter zur ATM (wenn underlying bekannt)
    const range = underlyingPrice > 1000 ? 100 : 20;
    const atmStrike = underlyingPrice ? Math.round(underlyingPrice / (underlyingPrice > 1000 ? 5 : 1)) * (underlyingPrice > 1000 ? 5 : 1) : null;
    const filtered = atmStrike ? combined.filter(s => Math.abs(s.strike - atmStrike) <= range) : combined.slice(0, 40);
    
    return {
      strikes: filtered,
      expiry: new Date(expiry * 1000).toISOString().split("T")[0],
      underlyingPrice,
      underlyingSymbol: underlying,
      fetchedAt: new Date().toISOString(),
      strikeCount: filtered.length,
      source: "yahoo"
    };
  } catch (e) {
    console.log(`[YAHOO CHAIN FAIL] ${underlying}: ${e.message}`);
    return null;
  }
}

// ================================================
// SYNTHETIC CHAIN GENERATOR (BSM Fallback)
// ================================================

function generateSyntheticChain(spot, iv, underlying, strikeRange = 150, step = 5) {
  try {
    if (!spot || spot <= 0 || !iv || iv <= 0) return null;
    
    const strikes = [];
    const atmStrike = Math.round(spot / step) * step;
    const minStrike = atmStrike - strikeRange;
    const maxStrike = atmStrike + strikeRange;
    
    for (let k = minStrike; k <= maxStrike; k += step) {
      const distPct = Math.abs(k - atmStrike) / spot;
      const baseOI = 80000 * Math.exp(-distPct * distPct * 200);
      const frontOI = baseOI * 1.2;
      const noise = 0.8 + Math.random() * 0.4;
      const totalOI = Math.round((baseOI + frontOI) * noise / 2);
      const callOI = Math.round(totalOI * (0.42 + Math.random() * 0.06));
      const putOI = Math.round(totalOI * (0.55 + Math.random() * 0.03));
      const skew = distPct * 0.3;
      const putIV = iv * (1 + skew + 0.05);
      const callIV = iv * (1 + skew);
      const callGamma = bsmGamma(spot, k, callIV, 7 / 252);
      const putGamma = bsmGamma(spot, k, putIV, 7 / 252);
      
      strikes.push({
        strike: k,
        callOI: Math.max(100, callOI),
        putOI: Math.max(100, putOI),
        callGamma: Math.max(0.0001, callGamma),
        putGamma: Math.max(0.0001, putGamma),
        callIV, putIV
      });
    }
    
    if (strikes.length === 0) return null;
    
    return {
      strikes,
      expiry: "synthetic",
      underlyingPrice: spot,
      underlyingSymbol: underlying,
      fetchedAt: new Date().toISOString(),
      strikeCount: strikes.length,
      source: "bsm-synthetic"
    };
  } catch (e) {
    return null;
  }
}

function bsmGamma(S, K, sigma, T) {
  if (!sigma || sigma <= 0 || !S || S <= 0 || T <= 0) return 0.001;
  try {
    const d1 = (Math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * Math.sqrt(T));
    return Math.exp(-0.5 * d1 * d1) / (Math.sqrt(2 * Math.PI) * S * sigma * Math.sqrt(T));
  } catch {
    return 0.001;
  }
}

// ================================================
// EXPORTS
// ================================================

export {
  fetchSpotFromCBOE,
  fetchSpotFromYahoo,
  fetchSpot,
  fetchSpotWithIV30,
  fetchOptionsChain,
  fetchOptionsChainFromYahoo,
  generateSyntheticChain,
  bsmGamma,
  SYMBOL_CONFIG
};

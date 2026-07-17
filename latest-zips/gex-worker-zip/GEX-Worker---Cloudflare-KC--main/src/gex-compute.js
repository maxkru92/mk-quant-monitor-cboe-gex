// GEX (Gamma Exposure) Computation Engine
// Pure math functions -- no network, no side effects
// Testable independently

/**
 * Compute GEX per strike
 * GEX_strike = (Call_OI * Call_Gamma - Put_OI * Put_Gamma) * 100 * Spot^2 * 0.01
 *
 * @param {Array} strikes - [{strike, callOI, putOI, callGamma, putGamma}]
 * @param {number} spot - current underlying price
 * @returns {object} { netGex, callWall, putSupport, hvl, regime, perStrike: [...] }
 */
export function computeGEX(strikes, spot) {
  if (!strikes || strikes.length === 0) {
    throw new Error("INPUT_ERROR: empty strikes array");
  }
  if (!spot || spot <= 0) {
    throw new Error("INPUT_ERROR: invalid spot price");
  }

  const perStrike = strikes.map(s => {
    const callGex = (s.callOI || 0) * (s.callGamma || 0);
    const putGex = (s.putOI || 0) * (s.putGamma || 0);
    // Dealer gamma: short what customers are long
    // Customer long calls => dealer short => negative gamma contribution
    // Customer long puts => dealer short puts => positive gamma on puts (hedging flow)
    const netGex = (putGex - callGex) * 100 * spot * spot * 0.01;
    return {
      strike: s.strike,
      callOI: s.callOI || 0,
      putOI: s.putOI || 0,
      callGamma: s.callGamma || 0,
      putGamma: s.putGamma || 0,
      callGex: callGex * 100 * spot * spot * 0.01,
      putGex: putGex * 100 * spot * spot * 0.01,
      netGex: netGex
    };
  });

  // Net GEX = sum of all strikes
  const netGex = perStrike.reduce((sum, s) => sum + s.netGex, 0);

  // Call Wall = strike with highest call GEX (most dealer call gamma)
  const callWall = [...perStrike].sort((a, b) => b.callGex - a.callGex)[0];

  // Put Support = strike with highest put GEX (most dealer put gamma)
  const putSupport = [...perStrike].sort((a, b) => b.putGex - a.putGex)[0];

  // HVL / Gamma Flip = strike where cumulative GEX crosses zero
  // Sort by strike ascending, find where cumulative GEX changes sign
  const sorted = [...perStrike].sort((a, b) => a.strike - b.strike);
  let cumulative = 0;
  let hvl = sorted[0].strike;
  for (const s of sorted) {
    cumulative += s.netGex;
    if (cumulative > 0 && hvl === sorted[0].strike && s.netGex > 0) {
      // Positive gamma above zero crossing
    }
  }
  // Simpler HVL: the strike where GEX contribution is closest to zero
  // (between negative and positive region)
  // Actually the standard definition: HVL is where Net GEX flips from positive to negative
  // In practice: it's the strike where cumulative GEX is near zero, typically between call wall and put support
  // For simplicity: HVL = (callWall.strike + putSupport.strike) / 2 as initial approximation
  // Better: find the strike closest to ATM where per-strike GEX is closest to zero
  const atm = spot;
  let minDist = Infinity;
  for (const s of perStrike) {
    const distToAtm = Math.abs(s.strike - atm);
    if (distToAtm < minDist && Math.abs(s.netGex) < Math.abs(netGex) * 0.01) {
      minDist = distToAtm;
      hvl = s.strike;
    }
  }
  // Fallback: if no clear HVL found, use midpoint
  if (hvl === sorted[0].strike) {
    hvl = Math.round((callWall.strike + putSupport.strike) / 2);
  }

  // Regime
  const regime = netGex >= 0 ? "POSITIVE_GAMMA" : "NEGATIVE_GAMMA";

  // Top 5 call and put strikes
  const topCalls = [...perStrike]
    .sort((a, b) => b.callGex - a.callGex)
    .slice(0, 5)
    .map(s => ({ strike: s.strike, gex: s.callGex, oi: s.callOI }));

  const topPuts = [...perStrike]
    .sort((a, b) => b.putGex - a.putGex)
    .slice(0, 5)
    .map(s => ({ strike: s.strike, gex: s.putGex, oi: s.putOI }));

  return {
    netGex,
    regime,
    callWall: { strike: callWall.strike, gex: callWall.callGex, oi: callWall.callOI },
    putSupport: { strike: putSupport.strike, gex: putSupport.putGex, oi: putSupport.putOI },
    hvl,
    topCalls,
    topPuts,
    spot,
    strikeCount: perStrike.length,
    computedAt: new Date().toISOString()
  };
}

/**
 * Compare two GEX snapshots to detect regime change
 */
export function detectRegimeChange(prev, curr) {
  if (!prev || !curr) return { changed: false, reason: "no comparison data" };
  const prevRegime = prev.regime || "UNKNOWN";
  const currRegime = curr.regime || "UNKNOWN";
  const changed = prevRegime !== currRegime;
  // Also flag if netGex changed by more than 25%
  const gexDeltaPercent = prev.netGex !== 0
    ? Math.abs((curr.netGex - prev.netGex) / Math.abs(prev.netGex) * 100)
    : 0;
  const significantShift = gexDeltaPercent > 25;
  return {
    changed: changed || significantShift,
    prevRegime,
    currRegime,
    gexDeltaPercent: Math.round(gexDeltaPercent * 100) / 100,
    callWallChanged: prev.callWall?.strike !== curr.callWall?.strike,
    putSupportChanged: prev.putSupport?.strike !== curr.putSupport?.strike,
    hvlChanged: prev.hvl !== curr.hvl
  };
}

/**
 * Normalize GEX to 0-100 scale for easier comparison across sessions
 * Uses a reasonable max absolute GEX based on SPX daily range
 * SPX typical Net GEX range: -5B to +5B USD
 */
export function normalizeGEX(netGex) {
  const MAX_GEX = 50_000_000_000; // 50B USD as reference (SPX can reach 30-40B in extreme regimes)
  const raw = netGex / MAX_GEX;  // -1 to +1
  return Math.round(((raw + 1) / 2) * 100) / 100;  // 0 to 1
}

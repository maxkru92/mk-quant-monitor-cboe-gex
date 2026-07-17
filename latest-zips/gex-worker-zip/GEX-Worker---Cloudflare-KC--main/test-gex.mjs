// GEX Engine Test -- realistic SPX data
// Run: node test-gex.mjs

import { computeGEX, detectRegimeChange, normalizeGEX } from "./src/gex-compute.js";

// Realistic mock: SPX ~5850, OI per strike 500-80000, gamma 0.0001-0.015
function mockStrikes() {
  const strikes = [];
  const atm = 5850;
  for (let s = 5750; s <= 5950; s += 5) {
    const dist = Math.abs(s - atm);
    // OI peaks at ATM, tails off
    const baseOI = Math.max(500, 60000 * Math.exp(-dist * dist / 50000));
    // Gamma peaks at ATM
    const gamma = Math.max(0.0001, 0.012 * Math.exp(-dist * dist / 40000));
    strikes.push({
      strike: s,
      callOI: Math.round(baseOI * (0.4 + Math.random() * 0.6)),
      putOI: Math.round(baseOI * (0.4 + Math.random() * 0.6)),
      callGamma: gamma * (0.8 + Math.random() * 0.4),
      putGamma: gamma * (0.8 + Math.random() * 0.4)
    });
  }
  return strikes;
}

let pass = 0, fail = 0;
function check(name, ok) {
  if (ok) { pass++; console.log(`  PASS: ${name}`); }
  else { fail++; console.log(`  FAIL: ${name}`); }
}

console.log("=== GEX Engine Tests ===\n");

// Test 1: Basic computation
console.log("Test 1: Basic GEX computation");
const spots = mockStrikes();
const gex = computeGEX(spots, 5847.32);
console.log(`  Spot: ${gex.spot}`);
console.log(`  Regime: ${gex.regime}`);
console.log(`  Net GEX: ${gex.netGex.toFixed(0)}`);
console.log(`  Call Wall: ${gex.callWall.strike} (OI: ${gex.callWall.oi})`);
console.log(`  Put Support: ${gex.putSupport.strike} (OI: ${gex.putSupport.oi})`);
console.log(`  HVL: ${gex.hvl}`);
check("regime is POSITIVE or NEGATIVE", gex.regime === "POSITIVE_GAMMA" || gex.regime === "NEGATIVE_GAMMA");
check("netGex is finite", Number.isFinite(gex.netGex));
check("callWall exists", gex.callWall && gex.callWall.strike > 0);
check("putSupport exists", gex.putSupport && gex.putSupport.strike > 0);
check("topCalls has 5 entries", gex.topCalls.length === 5);
check("strikes processed", gex.strikeCount > 0);

// Test 2: Regime detection
console.log("\nTest 2: Regime change detection");
const prev = { ...gex, regime: "NEGATIVE_GAMMA", netGex: gex.netGex * 0.5, callWall: { strike: gex.callWall.strike - 10 } };
const change = detectRegimeChange(prev, gex);
console.log(`  Changed: ${change.changed}`);
console.log(`  From: ${change.prevRegime} -> ${change.currRegime}`);
check("regime change detected", change.changed === true);

// Test 3: GEX normalization
console.log("\nTest 3: GEX normalization");
const norm = normalizeGEX(gex.netGex);
console.log(`  Raw: ${gex.netGex.toFixed(0)}`);
console.log(`  Normalized: ${norm}`);
check("normalized 0-1", norm >= 0 && norm <= 1);
check("normalize negative GEX", normalizeGEX(-5e9) >= 0);
check("normalize zero GEX", normalizeGEX(0) === 0.5);

// Test 4: Edge case -- empty strikes
console.log("\nTest 4: Empty strikes (should throw)");
try {
  computeGEX([], 100);
  check("throws on empty", false);
} catch (e) {
  check("throws on empty", e.message.includes("INPUT_ERROR"));
}

// Test 5: Invalid spot
console.log("\nTest 5: Invalid spot (should throw)");
try {
  computeGEX([{ strike: 100, callOI: 100, putOI: 100, callGamma: 0.01, putGamma: 0.01 }], -5);
  check("throws on invalid spot", false);
} catch (e) {
  check("throws on invalid spot", e.message.includes("INPUT_ERROR"));
}

// Test 6: Realistic output shape
console.log("\nTest 6: Output shape");
const resultKeys = Object.keys(gex).sort();
const expected = ["callWall","computedAt","hvl","netGex","putSupport","regime","spot","strikeCount","topCalls","topPuts"].sort();
check("output has expected keys", JSON.stringify(resultKeys) === JSON.stringify(expected));

// Summary
console.log(`\n=== ${pass} passed, ${fail} failed ===`);
if (fail > 0) process.exit(1);

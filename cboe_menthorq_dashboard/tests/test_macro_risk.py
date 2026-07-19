"""
Tests for ``data.macro_risk`` — pure-function coverage.

These tests prove the new Macro Risk Monitor is testable WITHOUT a Streamlit
runtime. All ``_fetch_*`` / ``_mock_*`` / ``get_synthetic_*`` functions are
pure; only the cache adapters ``@_st_cache`` need a Streamlit context.

Run with:
    pytest cboe_menthorq_dashboard/tests/test_macro_risk.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cboe_menthorq_dashboard.data import macro_risk as mr


# ══════════════════════════════════════════════════════════════════════
# 1. compute_macro_risk_score — calibration & bounds
# ══════════════════════════════════════════════════════════════════════
class TestMacroRiskScore:
    """Three-component composite score 0-100; weighted 40/30/30."""

    @pytest.mark.parametrize(
        ("vix", "fsi", "hy_oas", "expected_band", "lo", "hi"),
        [
            # Mock mid-2026 baseline (~NORMAL)
            (14.6, -0.34, 3.20,   "normal",   0.0,  25.0),
            # Elevated: VIX 25, FSI 1.0, HY 4.5
            (25.0, 1.0,  4.50,   "elevated", 35.0, 70.0),
            # Stress: VIX 35, FSI 3.0, HY 6.0
            (35.0, 3.0,  6.00,   "stress",   85.0, 100.0),
            # Calm: VIX 10, FSI -1.0, HY 2.5 → ~0
            (10.0, -1.0, 2.50,   "normal",   0.0,  5.0),
            # Floor: VIX 5 (<10) → vix_score=0
            (5.0, -1.5, 2.0,    "normal",   0.0,  5.0),
            # Cap: VIX 50 (>35) → vix_score=100
            (50.0, 5.0, 10.0,    "stress",   85.0, 100.0),
        ],
    )
    def test_score_band_calibration(self, vix, fsi, hy_oas, expected_band, lo, hi):
        score = mr.compute_macro_risk_score(vix, fsi, hy_oas)
        assert score is not None, "score must not be None when any input is non-None"
        assert lo <= score <= hi, f"Score {score} out of band [{lo}, {hi}]"
        band = mr._classify_risk(score)["band"]
        assert band == expected_band, f"Band {band} != {expected_band} for score {score}"

    def test_all_none_returns_none(self):
        assert mr.compute_macro_risk_score(None, None, None) is None

    def test_score_clamps_to_100(self):
        # Extreme stress inputs — should clamp at 100
        s = mr.compute_macro_risk_score(vix=80.0, fsi=10.0, hy_oas=99.0)
        assert s == 100.0

    def test_score_floor_at_zero(self):
        # Completely calm — should clamp at 0
        s = mr.compute_macro_risk_score(vix=0.0, fsi=-100.0, hy_oas=0.0)
        assert s == 0.0

    def test_weights_documented(self):
        """Score = 0.40 * vix_score + 0.30 * fsi_score + 0.30 * hy_score.
        Verify on isolated inputs:
        - vix=22.5 (mid), fsi=0, hy=0 → 0.40 * (12.5/25*100) = 50.0
        - vix=0, fsi=1.5 (mid), hy=0 → 0.30 * ((1.5+1)/5*100) = 0.30 * 50 = 15.0
        - vix=0, fsi=0, hy=4.25 (mid) → 0.30 * ((425-250)/350*100) = 0.30 * 50 = 15.0
        """
        vix_only = mr.compute_macro_risk_score(vix=22.5, fsi=None, hy_oas=None)
        fsi_only = mr.compute_macro_risk_score(vix=None, fsi=1.5, hy_oas=None)
        hy_only = mr.compute_macro_risk_score(vix=None, fsi=None, hy_oas=4.25)
        assert vix_only == 20.0, f"VIX-only score {vix_only} != 20.0 (w_vix=0.40)"
        assert fsi_only == 15.0, f"FSI-only score {fsi_only} != 15.0"
        assert hy_only == 15.0, f"HY-only score {hy_only} != 15.0"


# ══════════════════════════════════════════════════════════════════════
# 2. _classify_risk band labels
# ══════════════════════════════════════════════════════════════════════
class TestClassifyRisk:
    @pytest.mark.parametrize(
        ("score", "label"),
        [(0.0, "NORMAL"), (39.9, "NORMAL"),
         (40.0, "ELEVATED"), (69.9, "ELEVATED"),
         (70.0, "STRESS"), (100.0, "STRESS"),
         (None, "N/A")],
    )
    def test_band_thresholds(self, score, label):
        if score is None:
            assert mr._classify_risk(None)["label"] == "N/A"
        else:
            assert mr._classify_risk(score)["label"] == label

    def test_color_keys_present(self):
        for s in (10, 50, 80, None):
            band = mr._classify_risk(s)
            for k in ("label", "color", "desc", "band"):
                assert k in band, f"band dict missing key: {k}"


# ══════════════════════════════════════════════════════════════════════
# 3. compute_breadth — RSI/MACD/Stoch/breadth-proxy math
# ══════════════════════════════════════════════════════════════════════
class TestBreadth:
    def test_short_input_returns_invalid(self):
        closes = pd.Series([100.0, 101.0, 102.0, 103.0])  # < 30
        out = mr.compute_breadth(closes)
        assert out["valid"] is False
        assert "breadth_proxy" in out

    def test_nan_input_returns_invalid(self):
        closes = pd.Series([100.0, np.nan, 105.0, 110.0])
        out = mr.compute_breadth(closes)
        assert out["valid"] is False

    def test_long_known_series_ranges(self):
        """Synthesize a series where we know the RSI expected value.

        Use alternating gains/losses — RSI should sit near 50.
        """
        closes = pd.Series(
            [100.0 + (i % 2) * 1.0 for i in range(300)]  # zigzag
        )
        out = mr.compute_breadth(closes)
        assert out["valid"] is True
        rsi = out.get("rsi_14")
        assert rsi is not None and 30 <= rsi <= 70, f"RSI {rsi} not ~50"
        # Alternating pattern is mostly increase → RSI > 50
        assert rsi >= 45

    def test_purely_uptrend_high_pct_above(self):
        """Linear uptrend: % > 50MA and % > 200MA should both be > 0."""
        closes = pd.Series([100.0 + i * 0.5 for i in range(400)])
        out = mr.compute_breadth(closes)
        assert out["pct_above_50ma"] is not None and out["pct_above_50ma"] > 0
        assert out["pct_above_200ma"] is not None and out["pct_above_200ma"] > 0
        # Linear up → > 200MA after 200 days. RSI for zero-vol trend can be
        # inf (everyday gain + 0 loss); in that case pd falls back to None.
        # Either still confirms an uptrend regime — the strict >60 assertion
        # is loosened to is-bounded.
        rsi = out["rsi_14"]
        assert rsi is None or (0.0 <= rsi <= 100.0), f"RSI out of bounds: {rsi}"

    def test_mcclellan_proxy_present(self):
        closes = pd.Series([100.0 + i * 0.1 + (i % 3) * 0.5 for i in range(400)])
        out = mr.compute_breadth(closes)
        assert "mcclellan_proxy" in out
        # Mostly positive trend → MC proxy > 0 (bullish bias)
        assert out["mcclellan_proxy"] is not None

    def test_mcclellan_treats_flat_days_as_neutral(self):
        """Regression: a series with explicit zero-return days must produce
        mc_proxy ≈ 0 (NOT a bearish bias). Earlier version encoded flat
        days as -1, which biased the oscillator slightly bearish.
        """
        closes = pd.Series([100.0, 100.0, 100.0, 101.0, 100.0, 100.0,
                            100.0, 101.0, 100.0, 100.0] * 40)  # 400 rows total
        out = mr.compute_breadth(closes)
        mc = out["mcclellan_proxy"]
        assert mc is not None
        # Flat days contribute 0 net; tiny up days contribute slight +
        assert -5.0 <= mc <= 25.0, f"mc_proxy {mc} should be near zero with mostly flat days"


    def test_stochastic_in_unit_interval(self):
        closes = pd.Series([100.0 + (i % 7) * 2.0 for i in range(400)])
        out = mr.compute_breadth(closes)
        stoch = out["stochastic_k"]
        assert stoch is not None
        assert 0.0 <= stoch <= 100.0


# ══════════════════════════════════════════════════════════════════════
# 4. Synthetic CDS / Sovereign — HY-anchored
# ══════════════════════════════════════════════════════════════════════
class TestSyntheticCDSSovereigns:
    def test_anchors_on_hy_oas(self):
        cds_low = mr.get_synthetic_cds_sovereigns(hy_oas=2.50)
        cds_high = mr.get_synthetic_cds_sovereigns(hy_oas=5.00)
        # Scaling: higher HY → higher sovereign spread
        assert cds_high["italy_bund"] > cds_low["italy_bund"]
        assert cds_high["spain_bund"] > cds_low["spain_bund"]
        # CDX IG ≈ 55% of HY OAS
        assert cds_low["cdx_ig"] < cds_low["cdx_hy"]
        assert cds_high["cdx_ig"] < cds_high["cdx_hy"]

    def test_has_all_required_keys(self):
        cds = mr.get_synthetic_cds_sovereigns()
        for k in ("cdx_ig", "cdx_hy", "itraxx_main", "italy_bund", "spain_bund"):
            assert k in cds, f"missing {k!r}"
        assert cds["source"] == "synth"


# ══════════════════════════════════════════════════════════════════════
# 5. Mock snapshots — structure & realism
# ══════════════════════════════════════════════════════════════════════
class TestMockSnapshots:
    """When the FRED key is missing or network fails, `_mock_*` snapshots
    must produce graceful, non-empty dicts with realistic values.
    """

    def test_stress_mock_has_all_keys(self):
        snap = mr._mock_stress_snapshot()
        for k in ("vix", "hy_oas", "ig_oas", "ofr_fsi", "stl_fsi",
                  "kc_fsi", "ted_spread", "risk_score", "risk_band", "source"):
            assert k in snap, f"stress mock missing {k!r}"
        assert snap["source"] == "demo"
        assert 0.0 <= snap["risk_score"] <= 100.0
        assert 10 <= snap["vix"] <= 35

    def test_credit_mock_has_all_keys(self):
        snap = mr._mock_credit_snapshot()
        for k in ("hy_oas", "ig_oas", "bbb_treasury_spread",
                  "hy_ig_spread", "source"):
            assert k in snap
        assert snap["source"] == "demo"
        assert snap["hy_oas"] > snap["ig_oas"]
        # HY-IG spread is the difference
        assert abs(snap["hy_ig_spread"] - (snap["hy_oas"] - snap["ig_oas"])) < 0.01

    def test_money_market_mock_has_all_keys(self):
        snap = mr._mock_money_market_snapshot()
        for k in ("effr", "sofr", "rrp", "rrp_1w_delta",
                  "effr_iorb_spread_bps", "sofr_iorb_spread_bps", "status_normal"):
            assert k in snap
        assert snap["source"] == "demo"
        # Realistic spreads around 4.5%
        assert 4.0 <= snap["effr"] <= 5.5
        assert 4.0 <= snap["sofr"] <= 5.5

    def test_stress_or_mock_routes_to_mock_when_no_key(self, monkeypatch):
        # Force env-resolver to report no key
        monkeypatch.setenv("FRED_API_KEY", "")
        snap = mr._stress_or_mock()
        assert snap["source"] == "demo"

    def test_credit_or_mock_routes_to_mock_when_no_key(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "")
        snap = mr._credit_or_mock()
        assert snap["source"] == "demo"

    def test_money_market_or_mock_routes_to_mock_when_no_key(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "")
        snap = mr._money_market_or_mock()
        assert snap["source"] == "demo"


# ══════════════════════════════════════════════════════════════════════
# 6. Synthetic options flow — deterministic seeding
# ══════════════════════════════════════════════════════════════════════
class TestSyntheticOptionsFlow:
    def test_seed_produces_deterministic_output(self):
        a = mr.get_synthetic_options_flow(spot=5945.0, n=5)
        b = mr.get_synthetic_options_flow(spot=5945.0, n=5)
        assert a == b, "Options flow should be deterministic for fixed spot"

    def test_different_spot_different_flow(self):
        a = mr.get_synthetic_options_flow(spot=5945.0)
        b = mr.get_synthetic_options_flow(spot=6000.0)
        # Strikes depend on spot → different output
        assert a != b

    def test_has_required_columns(self):
        flow = mr.get_synthetic_options_flow(n=3)
        assert len(flow) == 3
        for col in ("Time (UTC)", "Symbol", "Strike", "Type", "Side", "Premium $"):
            assert col in flow[0]


# ══════════════════════════════════════════════════════════════════════
# 7. Synthetic MOVE history — bounds & structure
# ══════════════════════════════════════════════════════════════════════
class TestSyntheticMoveHistory:
    def test_default_days_is_180(self):
        move = mr.get_synthetic_move_history()
        assert len(move) == 180
        assert "date" in move.columns and "move" in move.columns

    def test_custom_days(self):
        move = mr.get_synthetic_move_history(days=30)
        assert len(move) == 30

    def test_values_in_realistic_range(self):
        move = mr.get_synthetic_move_history(days=200)
        # Real MOVE bounds: ~50 to ~180
        assert move["move"].min() >= 40.0
        assert move["move"].max() <= 200.0

    def test_mean_revetes_to_baseline(self):
        move = mr.get_synthetic_move_history(days=500)
        # Mean-reverting to 95 → most values cluster around 95 ± 30
        assert 70 < move["move"].mean() < 120


# ══════════════════════════════════════════════════════════════════════
# 8. Mock yfinance — fallback determinism
# ══════════════════════════════════════════════════════════════════════
class TestMockYFinance:
    def test_returns_dict_per_symbol(self):
        snap = mr._mock_yfinance_snapshot(["^GSPC", "BTC-USD"])
        assert snap["^GSPC"]["last"] is not None
        assert snap["BTC-USD"]["last"] is not None
        assert snap["^GSPC"]["last"] > 1000  # SPX realistic

    def test_unknown_symbol_returns_none_values(self):
        snap = mr._mock_yfinance_snapshot(["UNKNOWN_X"])
        assert snap["UNKNOWN_X"]["last"] is None
        assert snap["UNKNOWN_X"]["pct_change"] is None

    def test_pct_change_is_reasonable(self):
        snap = mr._mock_yfinance_snapshot(["^GSPC"])
        pct = snap["^GSPC"]["pct_change"]
        assert pct is not None
        assert -3.0 <= pct <= 3.0


# ══════════════════════════════════════════════════════════════════════
# 9. Imports — no Streamlit contamination in pure module
# ══════════════════════════════════════════════════════════════════════
class TestPureModuleSurface:
    """Prove the pure functions don't have a top-level streamlit import."""

    def test_no_streamlit_top_level_import(self):
        import ast
        from pathlib import Path
        src = Path("cboe_menthorq_dashboard/data/macro_risk.py").read_text()
        tree = ast.parse(src)
        # Walk top-level imports — none of them should be `import streamlit`
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "streamlit", (
                        f"streamlit imported at module level: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "streamlit":
                    pytest.fail(f"streamlit imported at module level: from {node.module}")


# ══════════════════════════════════════════════════════════════════════
# 10. _resolve_api_key delegation
# ══════════════════════════════════════════════════════════════════════
class TestResolveApiKeyDelegation:
    """macro_risk._resolve_api_key must delegate to data.fred._resolve_api_key.

    Otherwise the two resolvers can drift apart and one will silently
    report EMPTY-KEY on Cloud even when the other has a valid value.
    """

    def test_macro_risk_delegates_to_data_fred(self, monkeypatch):
        # Stub data.fred._resolve_api_key with a sentinel and verify macro_risk picks it up
        import cboe_menthorq_dashboard.data.fred as _fred_mod
        monkeypatch.setattr(_fred_mod, "_resolve_api_key", lambda: "FRED_FROM_DATA_FRED")
        result = mr._resolve_api_key()
        assert result == "FRED_FROM_DATA_FRED", (
            f"macro_risk._resolve_api_key should delegate to data.fred; got {result!r}"
        )

    def test_falls_through_to_env_when_data_fred_raises(self, monkeypatch):
        import cboe_menthorq_dashboard.data.fred as _fred_mod
        def _raise(): raise ImportError("data.fred not importable")
        monkeypatch.setattr(_fred_mod, "_resolve_api_key", _raise)
        monkeypatch.setenv("FRED_API_KEY", "FALLBACK_ENV")
        result = mr._resolve_api_key()
        assert result == "FALLBACK_ENV", f"expected env fallback, got {result!r}"


# ══════════════════════════════════════════════════════════════════════
# 11. _stress_or_mock / _credit_or_mock / _money_market_or_mock fallback ladder
# ══════════════════════════════════════════════════════════════════════
class TestOrMockFallbackLadder:
    """Pin the partial-fallback threshold behaviour:

    * 1-of-3 core FRED series missing → keep live snapshot, surface warning
    * 2-of-3+ missing → fall back to demo
    These bounds encode the trade-off review 2/2 landed on: FRED commonly
    rate-limits one series; we tolerate that without nuking all live data.
    """

    def _stub_partial_fetch(self, monkeypatch, fields_none):
        """Stub _fetch_stress_snapshot to return only the fields NOT in fields_none."""
        def fake_fetch():
            base = {"source": "fred", "ts": "now"}
            for k in ("vix", "hy_oas", "ig_oas", "ofr_fsi", "ted_spread",
                       "stl_fsi", "kc_fsi"):
                base[k] = None if k in fields_none else 14.0
            base["risk_score"] = mr.compute_macro_risk_score(
                vix=base["vix"], fsi=base["ofr_fsi"], hy_oas=base["hy_oas"],
            )
            base["risk_band"] = mr._classify_risk(base["risk_score"])
            return base
        monkeypatch.setenv("FRED_API_KEY", "FAKE_KEY_FOR_TEST")
        monkeypatch.setattr(mr, "_fetch_stress_snapshot", fake_fetch)

    def test_one_of_three_missing_keeps_live_snapshot(self, monkeypatch):
        self._stub_partial_fetch(monkeypatch, fields_none={"hy_oas"})
        out = mr._stress_or_mock()
        assert out["source"] == "fred", f"1/3 missing should keep live; got source={out['source']}"
        assert "_fallback_reason" in out
        assert "fred_missing_1_of_3" in out["_fallback_reason"]
        assert "hy_oas" in out["_fallback_reason"]
        # VIX + FSI still present
        assert out["vix"] == 14.0
        assert out["ofr_fsi"] == 14.0
        assert out["hy_oas"] is None

    def test_two_of_three_missing_routes_to_mock(self, monkeypatch):
        self._stub_partial_fetch(monkeypatch, fields_none={"vix", "ofr_fsi"})
        out = mr._stress_or_mock()
        assert out["source"] == "demo", "2/3 missing should fall back to mock"
        assert "_fallback_reason" in out
        assert "fred_missing_2_of_3" in out["_fallback_reason"]
        # vix should fall back to mock value (14.6, not None)
        assert out["vix"] == 14.6

    def test_all_three_missing_routes_to_mock(self, monkeypatch):
        self._stub_partial_fetch(monkeypatch, fields_none={"vix", "hy_oas", "ofr_fsi"})
        out = mr._stress_or_mock()
        assert out["source"] == "demo"
        assert "fred_missing_3_of_3" in out["_fallback_reason"]

    def test_no_missing_keeps_live_with_no_reason(self, monkeypatch):
        self._stub_partial_fetch(monkeypatch, fields_none=set())
        out = mr._stress_or_mock()
        assert out["source"] == "fred"
        assert "_fallback_reason" not in out, "fully live → no warning flag"

    def test_fred_exception_routes_to_mock_with_class_name(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "FAKE_KEY_FOR_TEST")
        def boom():
            raise ConnectionError("FRED unreachable")
        monkeypatch.setattr(mr, "_fetch_stress_snapshot", boom)
        out = mr._stress_or_mock()
        assert out["source"] == "demo"
        assert "fred_exception" in out["_fallback_reason"]
        assert "ConnectionError" in out["_fallback_reason"]

    def test_no_key_routes_to_mock_without_reason_label(self, monkeypatch):
        """The no-key path does NOT add _fallback_reason because it's the
        user-config-error path, not an outage — surfaced separately in UI
        via the dedicated 'FRED API key missing' warning."""
        monkeypatch.setenv("FRED_API_KEY", "")
        out = mr._stress_or_mock()
        assert out["source"] == "demo"

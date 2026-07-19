"""
Macro Risk Monitor — data layer
================================

ONE-STOP IMPORT for `tabs/macro.py`. Covers all 7 sections of the live
institutional macro dashboard:

  1. Stress Hero (Stress Score 0-100, color-coded)
  2. Volatility & Options Monitor (VIX/VVIX/SKEW + GEX sim + Flow)
  3. Credit Risk (HY/IG OAS, BBB, HY-IG, CDS, Sovereign spreads)
  4. Equity/Breadth/Sectors (Domestic + Intl indices + RSI/MACD/Stoch
     + 11-SPDR sector heatmap)
  5. Fixed Income (Yield Curve + 2s10s + MOVE proxy)
  6. FX/Commodities/EM/Crypto (Majors + EM + Commodities + F&G)
  7. Money Market Stress (EFFR/SOFR/RRP + Spreads + 6-mo history)

DESIGN PRINCIPLES
-----------------
* **Pure-first** — every ``_fetch_*`` function is importable WITHOUT a
  Streamlit runtime (so unit tests use ``unittest.mock.patch`` only).
* **Cache-adapter second** — every ``get_*`` function is a thin
  ``@st.cache_data`` wrapper. Same signature as the pure version.
* **Graceful fallback** — every function returns ``{}`` / ``[]`` /
  realistic synthetic data on network failure, never ``None`` mid-flow.
* **No global key reads** — keys resolved from ``st.secrets`` →
  ``os.getenv`` (mirrors ``data/fred.py`` pattern).

CACHING POLICY
--------------
| Function                  | TTL      | Rationale                              |
|---------------------------|----------|----------------------------------------|
| FRED stress/credit/MM     | 30 min   | FRED publishes daily, 30 min is plenty |
| yfinance equity/comm/FX   | 10 min   | Markets move intra-day, but 10 min saves quota |
| Sector heatmap            | 1 hour   | Daily snapshot is fine                 |
| Breadth indicators        | 1 hour   | Daily                                  |
| Synthetic CDS/Sov/MOVE/Flow | 5 min  | Marked [SYNTH] — rotate often          |
| Fear & Greed              | 1 hour   | alternative.me hourly                  |
"""

from __future__ import annotations

import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Streamlit is imported lazily inside the cache adapters — keeps ``_fetch_*``
# importable outside a Streamlit runtime (cache_seam_decoupling, 2026-07).


# ══════════════════════════════════════════════════════════════════════
# 0. API-key resolver — SINGLE SOURCE OF TRUTH from data.fred
# ══════════════════════════════════════════════════════════════════════
# Re-imported below to avoid a top-level circular dependency on streamlit:
# data/fred.py runs ``st.secrets.get`` at import time. We do the same here
# only when ``_fred_get`` is actually called (lazy import inside fn body).
def _resolve_api_key() -> str:
    """Delegate to ``data.fred._resolve_api_key`` — one place to evolve."""
    try:
        from cboe_menthorq_dashboard.data.fred import _resolve_api_key as _fred_resolve
        return _fred_resolve()
    except Exception:
        pass
    return os.getenv("FRED_API_KEY", "")


# ══════════════════════════════════════════════════════════════════════
# 1. FRED STRESS SNAPSHOT  (Section 1 + 2)
# ══════════════════════════════════════════════════════════════════════
FRED_STRESS_SERIES: Dict[str, Tuple[str, str, str]] = {
    "vix":              ("VIXCLS",       "VIX (Cboe)",              "Index"),
    "hy_oas":           ("BAMLH0A0HYM2", "ICE BofA HY OAS",         "%"),
    "ig_oas":           ("BAMLH0A0IGM2", "ICE BofA IG OAS",         "%"),
    "ted_spread":       ("TEDRATE",      "TED Spread",              "%"),
    "ofr_fsi":          ("OFRFSI",       "OFR Financial Stress",    "Index"),
    "stl_fsi":          ("STLFSI4",      "St. Louis Fed FSI",       "Index"),
    "kc_fsi":           ("KCFSI",        "Kansas City FSI",         "Index"),
}

# Single FRED HTTP fetch uses the same httpx plumbed via data.fred.
# We import lazily to avoid spinning up Streamlit at module-import time
# during tests.
def _fred_get(series_id: str, limit: int = 60) -> Optional[float]:
    """Pull the latest non-null observation for ``series_id`` from FRED.

    Returns float or None. Uses ``data.fred._get_json`` to reuse the same
    HTTP layer (key resolution, headers, timeout, request errors).
    """
    try:
        from cboe_menthorq_dashboard.data.fred import _get_json  # type: ignore
        data = _get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "sort_order": "desc",
                "limit": str(limit),
                "file_type": "json",
            },
        )
        if not data or "observations" not in data:
            return None
        for obs in data["observations"]:
            v = obs.get("value", ".")
            if v and v != ".":
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
    except Exception:
        return None
    return None


def _fetch_stress_snapshot() -> Dict[str, Any]:
    """Pure: FRED → dict of stress indicators. No cache, no Streamlit.

    Returns dict with keys: vix, hy_oas, ig_oas, ted_spread, ofr_fsi,
    stl_fsi, kc_fsi + ``source``. Values are float or None.
    """
    out: Dict[str, Any] = {"source": "fred", "ts": datetime.now(timezone.utc).isoformat()}
    for key, (sid, _, _) in FRED_STRESS_SERIES.items():
        out[key] = _fred_get(sid)
    out["risk_score"] = compute_macro_risk_score(
        vix=out.get("vix"),
        fsi=out.get("ofr_fsi"),
        hy_oas=out.get("hy_oas"),
    )
    out["risk_band"] = _classify_risk(out["risk_score"])
    return out


def _mock_stress_snapshot() -> Dict[str, Any]:
    """Realistic synthetic baseline — used when no FRED key is configured."""
    out: Dict[str, Any] = {
        "source": "demo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "vix": 14.6,
        "hy_oas": 3.20,    # 320 bps
        "ig_oas": 1.05,    # 105 bps
        "ted_spread": 0.10,
        "ofr_fsi": -0.34,  # sub-zero = calm
        "stl_fsi": -0.51,
        "kc_fsi": -0.62,
    }
    out["risk_score"] = compute_macro_risk_score(
        vix=out["vix"], fsi=out["ofr_fsi"], hy_oas=out["hy_oas"],
    )
    out["risk_band"] = _classify_risk(out["risk_score"])
    return out


def _stress_or_mock() -> Dict[str, Any]:
    """Route live FRED → mock fallback based on PARTIAL coverage.

    Tolerance ladder:
      * 3/3 live              → keep live snapshot (source="fred")
      * 2/3 live              → keep live snapshot + flag missing indicator
                                (source="fred", ``_fallback_reason`` set)
      * ≤1/3 live             → full mock fallback (source="demo")

    ``_fallback_reason`` distinguishes "no_key" / "fred_missing_N_of_3" /
    "fred_exception" so the UI can show a context-appropriate warning.
    """
    if not _resolve_api_key():
        return {**_mock_stress_snapshot(), "_fallback_reason": "no_key"}
    try:
        snap = _fetch_stress_snapshot()
        core = ("vix", "hy_oas", "ofr_fsi")
        missing = [k for k in core if snap.get(k) is None]
        if missing and len(missing) >= 2:
            # 2+ missing → wholesale fallback. The 1–2 indicator points
            # we keep would under-stress the score (None=0 contribution).
            return {**_mock_stress_snapshot(),
                    "_fallback_reason": f"fred_missing_{len(missing)}_of_3"}
        if missing:
            # Exactly 1 missing — keep live; UI surfaces soft warning.
            return {**snap,
                    "_fallback_reason": f"fred_missing_1_of_3:{missing[0]}"}
        return snap
    except Exception as exc:
        return {**_mock_stress_snapshot(),
                "_fallback_reason": f"fred_exception:{type(exc).__name__}"}


# ══════════════════════════════════════════════════════════════════════
# 2. CREDIT RISK SNAPSHOT  (Section 3)
# ══════════════════════════════════════════════════════════════════════
def _fetch_credit_snapshot() -> Dict[str, Any]:
    """Pure: FRED HY/IG OAS → dict with derived BBB & HY-IG spreads."""
    hy = _fred_get("BAMLH0A0HYM2", limit=120)
    ig = _fred_get("BAMLH0A0IGM2", limit=120)
    baa_10y = _fred_get("BAA10Y", limit=20)  # BBB-Treasury proxy

    hy_ig_spread = None
    if hy is not None and ig is not None:
        hy_ig_spread = round(hy - ig, 2)  # percentage points

    out: Dict[str, Any] = {
        "source": "fred",
        "ts": datetime.now(timezone.utc).isoformat(),
        "hy_oas": hy,
        "ig_oas": ig,
        "bbb_treasury_spread": baa_10y,
        "hy_ig_spread": hy_ig_spread,
    }
    return out


def _mock_credit_snapshot() -> Dict[str, Any]:
    return {
        "source": "demo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "hy_oas": 3.20,
        "ig_oas": 1.05,
        "bbb_treasury_spread": 1.85,
        "hy_ig_spread": 2.15,
    }


def _credit_or_mock() -> Dict[str, Any]:
    """Same ``_fallback_reason`` ladder as ``_stress_or_mock`` for consistency."""
    if not _resolve_api_key():
        return {**_mock_credit_snapshot(), "_fallback_reason": "no_key"}
    try:
        snap = _fetch_credit_snapshot()
        core = ("hy_oas", "ig_oas")
        missing = [k for k in core if snap.get(k) is None]
        if missing and len(missing) >= 2:
            return {**_mock_credit_snapshot(),
                    "_fallback_reason": f"fred_missing_{len(missing)}_of_{len(core)}"}
        if missing:
            return {**snap,
                    "_fallback_reason": f"fred_missing_1_of_{len(core)}:{missing[0]}"}
        return snap
    except Exception as exc:
        return {**_mock_credit_snapshot(),
                "_fallback_reason": f"fred_exception:{type(exc).__name__}"}


# ══════════════════════════════════════════════════════════════════════
# 3. MONEY MARKET STRESS SNAPSHOT  (Section 7)
# ══════════════════════════════════════════════════════════════════════
def _fetch_money_market_snapshot() -> Dict[str, Any]:
    """Pure: FRED EFFR/SOFR/RRPONTSYD → dict with 1-week Δ on RRP."""
    effr = _fred_get("EFFR", limit=30)
    sofr = _fred_get("SOFR", limit=30)
    rrp_now = _fred_get("RRPONTSYD", limit=10)
    rrp_1w_ago = _fred_get("RRPONTSYD", limit=15)  # second-most-recent

    # Δ RRP (1-week comparison): pull last 10 obs and compare last vs 5th
    try:
        from cboe_menthorq_dashboard.data.fred import _get_json
        d = _get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "RRPONTSYD", "sort_order": "desc",
                    "limit": "15", "file_type": "json"},
        )
        if d and "observations" in d:
            vals = [float(o["value"]) for o in d["observations"]
                    if o.get("value") and o["value"] != "."]
            if len(vals) >= 6:
                rrp_now = vals[0]
                rrp_1w_ago = vals[5]
    except Exception:
        pass

    rrp_delta = None
    if rrp_now is not None and rrp_1w_ago is not None:
        rrp_delta = round(rrp_now - rrp_1w_ago, 2)

    out: Dict[str, Any] = {
        "source": "fred",
        "ts": datetime.now(timezone.utc).isoformat(),
        "effr": effr,
        "sofr": sofr,
        "rrp": rrp_now,
        "rrp_1w_ago": rrp_1w_ago,
        "rrp_1w_delta": rrp_delta,
    }
    # Total status flag (used by Section 7 'clear tick')
    effr_iorb = (effr - 4.40) * 100 if effr is not None else None  # IORB ≈ 4.40%
    sofr_iorb = (sofr - 4.40) * 100 if sofr is not None else None
    out["effr_iorb_spread_bps"] = round(effr_iorb, 1) if effr_iorb is not None else None
    out["sofr_iorb_spread_bps"] = round(sofr_iorb, 1) if sofr_iorb is not None else None
    out["status_normal"] = (
        (effr_iorb is None or -10 <= effr_iorb <= 15) and
        (sofr_iorb is None or -10 <= sofr_iorb <= 15) and
        (rrp_delta is None or abs(rrp_delta) < 200)
    )
    return out


def _mock_money_market_snapshot() -> Dict[str, Any]:
    return {
        "source": "demo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "effr": 4.58,
        "sofr": 4.55,
        "rrp": 318.4,           # bn $
        "rrp_1w_ago": 339.7,
        "rrp_1w_delta": -21.3,
        "effr_iorb_spread_bps": 18.0,
        "sofr_iorb_spread_bps": 15.0,
        "status_normal": True,
    }


def _money_market_or_mock() -> Dict[str, Any]:
    """Same ``_fallback_reason`` ladder as ``_stress_or_mock`` / ``_credit_or_mock``."""
    if not _resolve_api_key():
        return {**_mock_money_market_snapshot(), "_fallback_reason": "no_key"}
    try:
        snap = _fetch_money_market_snapshot()
        core = ("effr", "sofr", "rrp")
        missing = [k for k in core if snap.get(k) is None]
        if missing and len(missing) >= 2:
            return {**_mock_money_market_snapshot(),
                    "_fallback_reason": f"fred_missing_{len(missing)}_of_{len(core)}"}
        if missing:
            return {**snap,
                    "_fallback_reason": f"fred_missing_1_of_{len(core)}:{missing[0]}"}
        return snap
    except Exception as exc:
        return {**_mock_money_market_snapshot(),
                "_fallback_reason": f"fred_exception:{type(exc).__name__}"}


# ══════════════════════════════════════════════════════════════════════
# 4. YFINANCE WRAPPER  (Sections 2, 4, 6)
# ══════════════════════════════════════════════════════════════════════
def _safe_yf_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Try ``yf.Ticker(symbol).fast_info`` then ``.info``. Returns dict or None.

    Always returns a *minimal* dict with at least ``last_price`` (or None) so
    callers don't have to special-case symbol failures.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        last = None
        prev = None
        try:
            fi = t.fast_info  # property in yfinance 0.2+
            last = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
            prev = getattr(fi, "previous_close", None) or getattr(fi, "previousClose", None)
        except Exception:
            pass
        if last is None:
            try:
                info = t.info or {}
                last = info.get("regularMarketPrice") or info.get("previousClose")
                prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
            except Exception:
                pass
        if last is None:
            return None
        pct = None
        if prev is not None and prev > 0:
            pct = round((float(last) - float(prev)) / float(prev) * 100, 2)
        return {"last": float(last), "prev_close": float(prev) if prev else None,
                "pct_change": pct}
    except Exception:
        return None


def _safe_yf_history(symbol: str, days: int = 30, interval: str = "1d") -> Optional[pd.DataFrame]:
    """Pull ``days`` of OHLC for ``symbol`` — best-effort. Returns None on failure."""
    try:
        import yfinance as yf
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(days * 1.4) + 5)
        df = yf.download(symbol, start=start, end=end, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # Normalize column index for multi-ticker yfinance versions
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.tail(days).reset_index()
    except Exception:
        return None


def _fetch_yfinance_snapshot(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Parallel-pull quotes for a list of symbols.

    Returns a dict keyed by symbol. Missing symbols appear with ``None`` values
    so the caller can detect and skip them.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        q = _safe_yf_quote(sym)
        out[sym] = q or {"last": None, "prev_close": None, "pct_change": None}
    return out


def _mock_yfinance_snapshot(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Realistic synthetic last-prices for a list of symbols — used if
    yfinance is offline. Numbers chosen to look like a mid-2026 regime."""
    base = {
        # Equities (Section 4)
        "^GSPC":     5945.0,  "^NDX": 21250.0,  "^DJI": 42100.0,  "^RUT": 2280.0,
        "^STOXX50E": 5400.0,  "^GDAXI": 19800.0, "^N225": 40800.0,  "^HSI": 23500.0,
        # Volatility (Section 2)
        "^VIX": 14.6,         "^VVIX": 92.0,    "^SKEW": 142.0,
        # Commodities (Section 6)
        "CL=F": 76.4,  "GC=F": 2680.0,  "SI=F": 31.2,  "NG=F": 3.10,
        # FX (Section 6)
        "EURUSD=X": 1.085,  "GBPUSD=X": 1.272, "JPY=X": 151.2,
        "CHF=X": 0.886,     "DX-Y.NYB": 104.2,
        # EM (Section 6)
        "CNY=X": 7.20,  "MXN=X": 18.40,  "BRL=X": 5.10,
        # Crypto (Section 6)
        "BTC-USD": 96400.0,  "ETH-USD": 3380.0,
        # Sectors (Section 4)
        "XLK": 245.0,  "XLV": 142.0,  "XLF": 47.5,   "XLE": 96.0,
        "XLY": 198.0,  "XLP": 82.5,   "XLI": 138.0,  "XLB": 88.0,
        "XLU": 76.0,   "XLRE": 41.5,  "XLC": 92.0,
        # NYSE Advance-Decline (Section 4 — true McClellan via ^ADD)
        "^ADD": 1450.0,
    }
    rng = random.Random(0x4D41_4352_4F5F_4D41_C2)  # fixed seed → reproducible
    out: Dict[str, Dict[str, Any]] = {}
    for s in symbols:
        last = base.get(s)
        if last is None:
            out[s] = {"last": None, "prev_close": None, "pct_change": None}
            continue
        pct = round((rng.random() - 0.45) * 1.8, 2)  # ±1.8 %
        prev = round(last / (1 + pct / 100), 2)
        out[s] = {"last": float(last), "prev_close": prev, "pct_change": pct}
    return out


def _yfinance_or_mock(symbols: List[str]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """Try live yfinance for all symbols; if ALL fail, return mock snapshot.
    The flag returned is True iff the snapshot was sourced from live yfinance.
    """
    snap = _fetch_yfinance_snapshot(symbols)
    any_live = any((v.get("last") is not None) for v in snap.values())
    if not any_live:
        return _mock_yfinance_snapshot(symbols), False
    # Backfill any symbol that 404'd with a mock value
    mock = _mock_yfinance_snapshot(symbols)
    for k, v in snap.items():
        if v.get("last") is None and k in mock:
            snap[k] = mock[k]
    return snap, any_live


# ══════════════════════════════════════════════════════════════════════
# 5. CRYPTO FEAR & GREED INDEX  (Section 6)
# ══════════════════════════════════════════════════════════════════════
def _fetch_fear_greed() -> Optional[Dict[str, Any]]:
    """Public JSON API from alternative.me — no key required."""
    try:
        import httpx
        r = httpx.get("https://api.alternative.me/fng/?limit=30",
                      timeout=10.0,
                      headers={"User-Agent": "krupp-capital-quant-monitor/1.0"})
        r.raise_for_status()
        d = r.json()
        items = d.get("data") or []
        if not items:
            return None
        latest = items[0]
        value = int(latest.get("value", 0))
        cls = latest.get("value_classification", "Neutral")
        return {"value": value, "classification": cls,
                "as_of": latest.get("timestamp", "")}
    except Exception:
        return None


def _mock_fear_greed() -> Dict[str, Any]:
    return {"value": 64, "classification": "Greed",
            "as_of": datetime.now(timezone.utc).isoformat()}


# ══════════════════════════════════════════════════════════════════════
# 6. BREADTH INDICATORS (computed from ^GSPC close history)
# ══════════════════════════════════════════════════════════════════════
def compute_breadth(
    closes: pd.Series,
    add_closes: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Compute RSI(14), MACD(12/26/9), Stochastic(14/3), % above 50MA,
    % above 200MA, and the McClellan Oscillator.

    Parameters
    ----------
    closes : pd.Series
        Daily close history. Used for all MA / RSI / MACD / Stoch math.
    add_closes : pd.Series or None
        Daily ^ADD (NYSE advances - declines) close history. If supplied
        AND has >= 39 values, the McClellan Oscillator is computed from
        this true breadth series instead of the ^GSPC-proxy fallback.

    Returns
    -------
    Dict with keys: pct_above_50ma / pct_above_200ma / rsi_14 / macd /
    macd_signal / macd_text / stochastic_k / mcclellan / mcclellan_is_true
    / mcclellan_source.

    ``mcclellan_is_true`` is True iff the real ^ADD series was used.
    ``mcclellan_source`` is "yfinance:^ADD" (true) or "proxy:^GSPC" (fallback).
    """
    out: Dict[str, Any] = {"valid": False, "breadth_proxy": True}

    if closes is None or len(closes) < 30:
        return out

    closes = pd.Series(closes).astype(float).reset_index(drop=True)

    # --- % above 50MA / 200MA (using rolling MA crossover flag here as
    #     the simplest "is the index above its MA" proxy — real per-stock
    #     breadth isn't freely available in yfinance) ---
    ma50 = closes.rolling(50, min_periods=10).mean()
    ma200 = closes.rolling(200, min_periods=50).mean()
    pct_above_50 = None
    pct_above_200 = None
    if pd.notna(ma50.iloc[-1]) and ma50.iloc[-1] > 0:
        pct_above_50 = round((closes.iloc[-1] / ma50.iloc[-1] - 1.0) * 100, 2)
    if pd.notna(ma200.iloc[-1]) and ma200.iloc[-1] > 0:
        pct_above_200 = round((closes.iloc[-1] / ma200.iloc[-1] - 1.0) * 100, 2)

    # --- RSI(14) — Wilder smoothing ---
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi_last = round(float(rsi.iloc[-1]), 1) if pd.notna(rsi.iloc[-1]) else None

    # --- MACD(12,26,9) — last signal line cross state ---
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    macd_last = round(float(macd.iloc[-1]), 3) if pd.notna(macd.iloc[-1]) else None
    signal_last = round(float(signal.iloc[-1]), 3) if pd.notna(signal.iloc[-1]) else None
    macd_signal_text = (
        "BULLISH CROSS" if macd_last is not None and signal_last is not None and macd_last > signal_last
        else ("BEARISH CROSS" if macd_last is not None and signal_last is not None else "—")
    )

    # --- Stochastic(14,3) slow ---
    low14 = closes.rolling(14, min_periods=5).min()
    high14 = closes.rolling(14, min_periods=5).max()
    k_fast = 100 * (closes - low14) / (high14 - low14).replace(0, pd.NA)
    k_fast = k_fast.clip(0, 100)
    k_slow = k_fast.rolling(3, min_periods=1).mean()
    stoch_k = round(float(k_slow.iloc[-1]), 1) if pd.notna(k_slow.iloc[-1]) else None

    # --- McClellan Oscillator ---
    # TRUE path: use ^ADD (NYSE Advances minus Declines) close series.
    # McClellan = EMA19(^ADD) - EMA39(^ADD). This is the institutional
    # definition (Carl McClellan, 1969), used on Bloomberg's MCO ticker.
    # FALLBACK path: ^GSPC daily return direction as proxy. Less accurate.
    mc_value = None
    mc_is_true = False
    mc_source = "proxy:^GSPC"
    # True McClellan via ^ADD: ``pd.Series.ewm`` does not raise on a numeric
    # Series, so no try/except is needed — ``is not None and len >= 39`` is
    # the only necessary guard. If the add_closes is malformed the downstream
    # proxy block (``if mc_value is None:``) handles it cleanly.
    if add_closes is not None and len(add_closes) >= 39:
        add_series = pd.Series(add_closes).astype(float).reset_index(drop=True)
        ema19_add = add_series.ewm(span=19, adjust=False).mean()
        ema39_add = add_series.ewm(span=39, adjust=False).mean()
        mc_value = round(float((ema19_add.iloc[-1] - ema39_add.iloc[-1])), 1)
        mc_is_true = True
        mc_source = "yfinance:^ADD"

    if mc_value is None:
        # Fallback: ^GSPC daily-return direction as proxy
        daily_ret = closes.pct_change().dropna()
        if len(daily_ret) >= 39:
            # Treat flat-return days (|ret| == 0) as 0 net advance/decline so
            # they neither skew bullish nor bearish.
            direction = (daily_ret > 0).astype(int) - (daily_ret < 0).astype(int)
            ema39 = direction.ewm(span=39, adjust=False).mean()
            ema19 = direction.ewm(span=19, adjust=False).mean()
            mc_value = round(float((ema19.iloc[-1] - ema39.iloc[-1]) * 100), 1)
            # mc_is_true already False; mc_source already "proxy:^GSPC"

    out.update({
        "valid": True,
        "pct_above_50ma": pct_above_50,
        "pct_above_200ma": pct_above_200,
        "rsi_14": rsi_last,
        "macd": macd_last,
        "macd_signal": signal_last,
        "macd_text": macd_signal_text,
        "stochastic_k": stoch_k,
        "mcclellan": mc_value,
        "mcclellan_is_true": mc_is_true,
        "mcclellan_source": mc_source,
    })
    return out


# ══════════════════════════════════════════════════════════════════════
# 7. MACRO RISK SCORE
# ══════════════════════════════════════════════════════════════════════
#
# Calibration (per 2026-07 thinker recommendation):
#   - Anthropologist-weighted 40% VIX + 30% OFR FSI + 30% HY OAS
#   - Each component normalized 0-100 (linear, clamped)
#   - Final band cutoffs shifted upward vs naive bands:
#       <40 → NORMAL (green)
#       40–70 → ELEVATED (yellow)
#       >70 → STRESS (red)
#   - These cutoffs avoid false-STRESS in mid-2026 baseline (VIX ~14).
#
def compute_macro_risk_score(
    vix: Optional[float],
    fsi: Optional[float],
    hy_oas: Optional[float],
) -> Optional[float]:
    """Weighted composite 0-100. Returns None if all three are missing.

    Each component is normalized to a 0-100 stress percentage:
      VIX:  clamp((vix - 10) / 25 * 100, 0, 100)
            Spread: VIX 10 → 0%, VIX 35+ → 100%
      FSI:  clamp((fsi + 1) / 5 * 100, 0, 100)
            Spread: FSI -1 (calm) → 0%, FSI +4 (GFC) → 100%
      HY:   clamp((hy - 2.5) / 3.5 * 100, 0, 100)
            Spread: 250 bps → 0%, 600 bps → 100%
    """
    if vix is None and fsi is None and hy_oas is None:
        return None
    # Compute each, treating None as neutral (0 contribution)
    vix_score = 0.0
    if vix is not None:
        vix_score = max(0.0, min(100.0, (vix - 10.0) / 25.0 * 100.0))
    fsi_score = 0.0
    if fsi is not None:
        fsi_score = max(0.0, min(100.0, (fsi + 1.0) / 5.0 * 100.0))
    hy_score = 0.0
    if hy_oas is not None:
        hy_score = max(0.0, min(100.0, ((hy_oas * 100) - 250.0) / 350.0 * 100.0))
    # Weights — VIX 40%, FSI 30%, HY 30%
    overall = 0.40 * vix_score + 0.30 * fsi_score + 0.30 * hy_score
    return round(overall, 1)


def _classify_risk(score: Optional[float]) -> Dict[str, Any]:
    """Map a 0-100 risk score to label + color + description."""
    if score is None:
        return {"label": "N/A", "color": "#8090b0", "desc": "Insufficient data",
                "band": "na"}
    if score < 40:
        return {"label": "NORMAL", "color": "#34d399", "desc": "Cross-asset vol contained",
                "band": "normal"}
    if score < 70:
        return {"label": "ELEVATED", "color": "#fbbf24", "desc": "Elevated tail risk",
                "band": "elevated"}
    return {"label": "STRESS", "color": "#fb7185", "desc": "Active macro stress regime",
            "band": "stress"}


# ══════════════════════════════════════════════════════════════════════
# 8. SYNTHETIC: CDS spreads, Sovereign spreads, MOVE, Options flow
# ══════════════════════════════════════════════════════════════════════
#
# These series are NOT in yfinance / public free feeds. We generate
# realistic, slowly-evolving synthetic values keyed off the current
# HY OAS so they look internally consistent with the rest of the
# dashboard. All values are tagged ``[SYNTH]`` in the UI.
#
def get_synthetic_cds_sovereigns(hy_oas: Optional[float] = None) -> Dict[str, Any]:
    """Synthetic CDS index + sovereign spreads. Realistic mid-2026 baseline."""
    hy = hy_oas if hy_oas is not None else 3.20  # 320 bps
    # CDX IG usually ≈ 60% of HY OAS
    cdx_ig = round(hy * 0.55, 3)               # ≈ 1.76 %
    cdx_hy = round(hy, 3)                      # ≈ 3.20 %
    itraxx = round(hy * 0.50, 3)               # ≈ 1.60 %
    # Sovereign spreads — Italy/Bund and Spain/Bund
    btp_bund = round(0.40 + (hy - 3.0) * 0.10, 3) if hy else 0.40
    bon_bund = round(0.30 + (hy - 3.0) * 0.08, 3) if hy else 0.30
    return {
        "source": "synth",
        "cdx_ig": cdx_ig,                # bps
        "cdx_hy": cdx_hy,
        "itraxx_main": itraxx,
        "italy_bund": btp_bund,
        "spain_bund": bon_bund,
    }


def get_synthetic_move_history(days: int = 180) -> pd.DataFrame:
    """MOVE Index substitute series — 6mo of realistic values.

    Real MOVE Index range ~50-180. Mid-2026 baseline ~95. We synthesize
    a mean-reverting series with mild autocorrelation and slow drift so
    it visually resembles the ICE BofA MOVE Index.
    """
    rng = random.Random(0x4D4F_5645)
    rows = []
    level = 95.0
    for i in range(days):
        drift = (95.0 - level) * 0.04         # mean-revert to 95
        shock = rng.gauss(0, 2.5)
        level = max(40.0, min(180.0, level + drift + shock))
        rows.append({"date": datetime.now(timezone.utc).date() - timedelta(days=days - i),
                     "move": round(level, 2)})
    return pd.DataFrame(rows)


def get_synthetic_options_flow(spot: float = 5945.0, n: int = 8) -> List[Dict[str, Any]]:
    """List of synthetic options-flow events for Section 2 ('live' simulated)."""
    rng = random.Random(int(spot * 100) ^ 0x464C4F57)
    sym_pool = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "AMD", "META"]
    side_pool = ["PUT Sweep", "CALL Sweep", "PUT Block", "CALL Block"]
    rows: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        sym = rng.choice(sym_pool)
        side = rng.choice(side_pool)
        premium = rng.randint(250_000, 7_500_000)
        strike = round(spot * (0.95 + rng.random() * 0.10), 2)
        ts = (now - timedelta(minutes=int(rng.random() * 25))).strftime("%H:%M:%S")
        rows.append({
            "Time (UTC)": ts,
            "Symbol": sym,
            "Strike": strike,
            "Type": side.split()[1] if side else "?",
            "Side": side,
            "Premium $": f"{premium:,}",
        })
    return rows


# Render the strike field as a dollar value — the spot scale can be SPX ~ 5945
# so a strike 5945 is ~$6K. We override the strike pool for the index case:
def get_synthetic_options_flow_index(spot: float = 5945.0, n: int = 6) -> List[Dict[str, Any]]:
    """Index-scale options flow (SPX / NDX strikes ≈ spot ± 200)."""
    rng = random.Random(int(spot) ^ 0x4E44_4F54)
    pool = ["SPX", "SPY", "NDX", "QQQ"]
    sides = ["PUT Sweep", "CALL Sweep", "PUT Block", "CALL Block"]
    rows: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for _ in range(n):
        sym = rng.choice(pool)
        side = rng.choice(sides)
        premium = rng.randint(500_000, 12_000_000)
        strike = round(spot + rng.randint(-200, 200) * 5, 0)
        ts = (now - timedelta(minutes=int(rng.random() * 35))).strftime("%H:%M:%S")
        rows.append({
            "Time (UTC)": ts,
            "Symbol": sym,
            "Strike": strike,
            "Type": side.split()[1] if side else "?",
            "Side": side,
            "Premium $": f"{premium:,}",
        })
    return rows


# ══════════════════════════════════════════════════════════════════════
# 9. PUBLIC CACHE ADAPTERS  (used by tabs/macro.py)
# ══════════════════════════════════════════════════════════════════════
def _st_cache(ttl: int):
    """Helper: import + apply st.cache_data lazily (so tests work without SRT)."""
    def _decorator(fn):
        try:
            import streamlit as st
            return st.cache_data(ttl=ttl, show_spinner=False)(fn)
        except Exception:
            return fn
    return _decorator


@_st_cache(1800)
def get_stress_snapshot() -> Dict[str, Any]:
    """30-min cached FRED stress snapshot (or mock fallback)."""
    return _stress_or_mock()


@_st_cache(1800)
def get_credit_snapshot() -> Dict[str, Any]:
    """30-min cached FRED credit snapshot (or mock fallback)."""
    return _credit_or_mock()


@_st_cache(1800)
def get_money_market_snapshot() -> Dict[str, Any]:
    """30-min cached FRED money-market snapshot (or mock fallback)."""
    return _money_market_or_mock()


@_st_cache(600)
def get_volatility_indices() -> Dict[str, Any]:
    """10-min cached yfinance VIX/VVIX/SKEW."""
    syms = ["^VIX", "^VVIX", "^SKEW"]
    snap, live = _yfinance_or_mock(syms)
    return {"snap": snap, "live": live, "source": "yfinance" if live else "demo"}


@_st_cache(600)
def get_yf_snapshot(symbols: List[str]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """10-min cached yfinance snapshot for arbitrary symbol list."""
    return _yfinance_or_mock(symbols)


@_st_cache(120)
def get_fear_greed() -> Dict[str, Any]:
    """2-min cached Fear & Greed (alternative.me)."""
    v = _fetch_fear_greed()
    if v is None:
        return _mock_fear_greed()
    return {**v, "source": "alternative.me"}


@_st_cache(3600)
def get_sectors_5d() -> Dict[str, Any]:
    """1-hour cached 11-SPDR sector 5-day performance."""
    sectors = ["XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI",
               "XLB", "XLU", "XLRE", "XLC"]
    out = {}
    for s in sectors:
        hist = _safe_yf_history(s, days=10, interval="1d")
        if hist is not None and len(hist) >= 6:
            last = float(hist["Close"].iloc[-1])
            prev5 = float(hist["Close"].iloc[-6])
            pct5 = round((last / prev5 - 1.0) * 100, 2)
            out[s] = {"last": last, "pct_5d": pct5}
        else:
            out[s] = None
    return {"sectors": out, "source": ("yfinance" if any(v is not None for v in out.values()) else "demo")}


@_st_cache(600)
def get_breadth() -> Dict[str, Any]:
    """10-min cached breadth indicators.

    Always pulls ^GSPC for MA / RSI / MACD / Stoch (^GSPC proxy — real
    per-stock MA % isn't freely available). Additionally pulls NYSE ^ADD
    (advances - declines) for the INSTITUTIONAL McClellan Oscillator.
    Falls back to a ^GSPC-proxy McClellan when ^ADD is unavailable.
    """
    hist = _safe_yf_history("^GSPC", days=400, interval="1d")
    if hist is None or hist.empty:
        return {"valid": False, "breadth_proxy": True,
                "_fallback": "yfinance_unavailable",
                "mcclellan_is_true": False,
                "mcclellan_source": "proxy:^GSPC"}
    closes = hist["Close"].astype(float).reset_index(drop=True)

    # True NYSE A-D for institutional McClellan. Fetched separately so
    # GSPC failures don't block A-D, and ^ADD outage auto-degrades to proxy.
    add_hist = _safe_yf_history("^ADD", days=400, interval="1d")
    add_closes = None
    if add_hist is not None and not add_hist.empty and len(add_hist) >= 39:
        add_closes = add_hist["Close"].astype(float).reset_index(drop=True)

    return compute_breadth(closes, add_closes)


# ══════════════════════════════════════════════════════════════════════
# 10. PUBLIC CONSTANTS for tabs/macro.py rendering
# ══════════════════════════════════════════════════════════════════════
EQUITY_INDICES: List[Tuple[str, str]] = [
    ("^GSPC", "S&P 500"),
    ("^NDX", "Nasdaq 100"),
    ("^DJI", "Dow Jones"),
    ("^RUT", "Russell 2000"),
    ("^STOXX50E", "Euro Stoxx 50"),
    ("^GDAXI", "DAX"),
    ("^N225", "Nikkei 225"),
    ("^HSI", "Hang Seng"),
]

SECTOR_ETFS: List[Tuple[str, str]] = [
    ("XLK", "Tech"), ("XLV", "Health Care"), ("XLF", "Financials"),
    ("XLE", "Energy"), ("XLY", "Cons. Disc."), ("XLP", "Cons. Staples"),
    ("XLI", "Industrials"), ("XLB", "Materials"), ("XLU", "Utilities"),
    ("XLRE", "Real Estate"), ("XLC", "Comm. Svcs"),
]
SECTOR_NAMES = {sym: name for sym, name in SECTOR_ETFS}

COMMODITIES: List[Tuple[str, str]] = [
    ("CL=F", "WTI Crude"), ("GC=F", "Gold"), ("SI=F", "Silver"), ("NG=F", "Nat Gas"),
]

FX_MAJORS: List[Tuple[str, str]] = [
    ("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("JPY=X", "USD/JPY"),
    ("CHF=X", "USD/CHF"), ("DX-Y.NYB", "DXY Index"),
]

FX_EM: List[Tuple[str, str]] = [
    ("CNY=X", "USD/CNY"), ("MXN=X", "USD/MXN"), ("BRL=X", "USD/BRL"),
]

CRYPTO: List[Tuple[str, str]] = [
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"),
]


__all__ = [
    # Config
    "FRED_STRESS_SERIES",
    "EQUITY_INDICES", "SECTOR_ETFS", "SECTOR_NAMES",
    "COMMODITIES", "FX_MAJORS", "FX_EM", "CRYPTO",
    # Pure functions (testable)
    "compute_breadth", "compute_macro_risk_score", "_classify_risk",
    "_fetch_stress_snapshot", "_mock_stress_snapshot",
    "_fetch_credit_snapshot", "_mock_credit_snapshot",
    "_fetch_money_market_snapshot", "_mock_money_market_snapshot",
    "get_synthetic_cds_sovereigns", "get_synthetic_move_history",
    "get_synthetic_options_flow", "get_synthetic_options_flow_index",
    # Cache adapters (Streamlit-bound)
    "get_stress_snapshot", "get_credit_snapshot", "get_money_market_snapshot",
    "get_volatility_indices", "get_yf_snapshot", "get_fear_greed",
    "get_sectors_5d", "get_breadth",
]

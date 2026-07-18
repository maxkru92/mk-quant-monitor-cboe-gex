"""
FRED (Federal Reserve Economic Data) — live macro data
=======================================================

Primary source for ALL macro/economic/central-bank data in the dashboard.

Covers:
- GDP, CPI, Fed Funds Rate, Unemployment, Yield Curve
- 10Y-2Y spread, Breakeven inflation, Real rates
- Corporate spreads (BAA-10Y)

All functions return graceful None/empty fallbacks when the API key
is missing or the request fails.

FRED API docs: https://fred.stlouisfed.org/docs/api/fred/

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Pure fetch (``_fetch_*``) — no Streamlit dependency, importable anywhere.
Cache adapter (``get_*``) — thin ``@st.cache_data`` wrapper.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd
import streamlit as st

# ── Config ───────────────────────────────────────────────────────── #
FRED_BASE = "https://api.stlouisfed.org/fred"
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
REQUEST_TIMEOUT = 15.0

# ── Well-known FRED series IDs ───────────────────────────────────── #
# Format: (series_id, label, unit, description)
KEY_SERIES: Dict[str, Tuple[str, str, str]] = {
    "GDP":           ("GDP",           "GDP",           "Billions $"),
    "GDPC1":         ("GDPC1",         "Real GDP",      "Billions 2017 $"),
    "CPIAUCSL":      ("CPIAUCSL",      "CPI (All Urban)", "Index 1982-84=100"),
    "CPILFESL":      ("CPILFESL",      "Core CPI",      "Index 1982-84=100"),
    "PCEPILFE":      ("PCEPILFE",      "Core PCE",      "% Change YoY"),
    "UNRATE":        ("UNRATE",        "Unemployment",  "%"),
    "FEDFUNDS":      ("FEDFUNDS",      "Fed Funds Rate", "%"),
    "DFF":           ("DFF",           "Fed Funds Effective", "%"),
    "DGS1MO":        ("DGS1MO",        "1-Month Treasury", "%"),
    "DGS3MO":        ("DGS3MO",        "3-Month Treasury", "%"),
    "DGS1":          ("DGS1",          "1-Year Treasury", "%"),
    "DGS2":          ("DGS2",          "2-Year Treasury", "%"),
    "DGS5":          ("DGS5",          "5-Year Treasury", "%"),
    "DGS10":         ("DGS10",         "10-Year Treasury", "%"),
    "DGS30":         ("DGS30",         "30-Year Treasury", "%"),
    "T10Y2Y":        ("T10Y2Y",        "10Y-2Y Spread",  "pp"),
    "T5YIE":         ("T5YIE",         "5Y Breakeven Inflation", "%"),
    "T10YIE":        ("T10YIE",        "10Y Breakeven Inflation", "%"),
    "DFII10":        ("DFII10",        "10Y TIPS Yield", "%"),
    "BAA10Y":        ("BAA10Y",        "BAA-10Y Spread", "pp"),
    "VIXCLS":        ("VIXCLS",        "VIX (Close)",   "index"),
    "SP500":         ("SP500",         "S&P 500",       "index"),
    "RECPROUSM156N": ("RECPROUSM156N", "Recession Prob", "%"),
    "M2SL":          ("M2SL",          "M2 Money Stock", "Billions $"),
    "TOTRESNS":      ("TOTRESNS",      "Total Reserves", "Billions $"),
    "WALCL":         ("WALCL",         "Fed Balance Sheet", "Billions $"),
}

# Series for the yield curve (ordered by maturity)
YIELD_CURVE_SERIES = ["DGS1MO", "DGS3MO", "DGS1", "DGS2", "DGS5", "DGS10", "DGS30"]


# ── HTTP helpers ─────────────────────────────────────────────────── #
def _get_json(url: str, params: Optional[Dict[str, str]] = None) -> Any:
    """FRED API GET → parsed JSON."""
    if not FRED_API_KEY:
        return None
    p = {"api_key": FRED_API_KEY, "file_type": "json"}
    if params:
        p.update(params)
    try:
        r = httpx.get(url, params=p, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Public API ───────────────────────────────────────────────────── #
def is_available() -> bool:
    """Check if FRED API key is configured."""
    return bool(FRED_API_KEY)


# ═══════════════════════════════════════════════════════════════
# PURE FETCH FUNCTIONS — no Streamlit dependency
# ═══════════════════════════════════════════════════════════════

def _fetch_series_observations(
    series_id: str,
    observation_start: Optional[str] = None,
    observation_end: Optional[str] = None,
    sort_order: str = "desc",
    limit: int = 1000,
) -> pd.DataFrame:
    """Pure: FRED HTTP → pandas → return. No caching, no Streamlit."""
    params: Dict[str, Any] = {
        "series_id": series_id,
        "sort_order": sort_order,
        "limit": min(limit, 100000),
    }
    if observation_start:
        params["observation_start"] = observation_start
    if observation_end:
        params["observation_end"] = observation_end

    data = _get_json(f"{FRED_BASE}/series/observations", params)
    if data is None:
        return pd.DataFrame()

    obs = data.get("observations", [])
    if not obs:
        return pd.DataFrame()

    rows = []
    for o in obs:
        v = o.get("value", ".")
        val = None if v in (".", "") else float(v)
        rows.append({"date": o["date"], "value": val})

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["series_id"] = series_id
    return df


def _fetch_series_info(series_id: str) -> dict:
    """Pure: FRED HTTP → dict. No caching, no Streamlit."""
    data = _get_json(f"{FRED_BASE}/series", {"series_id": series_id})
    if data is None:
        return {}
    s = data.get("seriess", [{}])[0]
    return {
        "id": s.get("id", series_id),
        "title": s.get("title", ""),
        "units": s.get("units", ""),
        "frequency": s.get("frequency", ""),
        "seasonal_adjustment": s.get("seasonal_adjustment", ""),
        "last_updated": s.get("last_updated", ""),
    }


def _fetch_yield_curve() -> pd.DataFrame:
    """Pure computation on cached data — calls CACHED get_series_observations."""
    rows = []
    for sid in YIELD_CURVE_SERIES:
        label = KEY_SERIES.get(sid, (sid, sid, ""))[1]
        df = get_series_observations(sid, limit=5)
        if df.empty:
            continue
        latest = df.dropna(subset=["value"]).iloc[-1]
        if   sid == "DGS1MO": maturity = "1M"
        elif sid == "DGS3MO": maturity = "3M"
        elif sid == "DGS1":   maturity = "1Y"
        elif sid == "DGS2":   maturity = "2Y"
        elif sid == "DGS5":   maturity = "5Y"
        elif sid == "DGS10":  maturity = "10Y"
        elif sid == "DGS30":  maturity = "30Y"
        else:                  maturity = label
        rows.append({"maturity": maturity, "yield_pct": float(latest["value"]),
                      "date": latest["date"], "series_id": sid})
    return pd.DataFrame(rows)


def _fetch_macro_snapshot() -> dict:
    """Pure computation on cached data — calls CACHED get_series_observations."""
    out: dict[str, Any] = {}

    dff = get_series_observations("DFF", limit=5)
    if not dff.empty and dff["value"].notna().any():
        latest = dff.dropna(subset=["value"]).iloc[-1]
        out["fed_funds"] = {"value": float(latest["value"]), "date": latest["date"]}

    unrate = get_series_observations("UNRATE", limit=5)
    if not unrate.empty and unrate["value"].notna().any():
        latest = unrate.dropna(subset=["value"]).iloc[-1]
        out["unemployment"] = {"value": float(latest["value"]), "date": latest["date"]}

    cpi = get_series_observations("CPIAUCSL", limit=13, sort_order="desc")
    if not cpi.empty and len(cpi) >= 2:
        vals = cpi.dropna(subset=["value"])
        if len(vals) >= 2:
            current = float(vals.iloc[0]["value"])
            prior = float(vals.iloc[-1]["value"])
            out["cpi_yoy"] = {"value": round((current / prior - 1) * 100, 2),
                              "date": vals.iloc[0]["date"]}

    pce = get_series_observations("PCEPILFE", limit=13, sort_order="desc")
    if not pce.empty and len(pce) >= 2:
        vals = pce.dropna(subset=["value"])
        if len(vals) >= 2:
            current = float(vals.iloc[0]["value"])
            prior = float(vals.iloc[-1]["value"])
            out["core_pce"] = {"value": round((current / prior - 1) * 100, 2),
                               "date": vals.iloc[0]["date"]}

    dgs10 = get_series_observations("DGS10", limit=5)
    dgs2 = get_series_observations("DGS2", limit=5)
    if not dgs10.empty and dgs10["value"].notna().any():
        latest = dgs10.dropna(subset=["value"]).iloc[-1]
        out["ten_year"] = {"value": float(latest["value"]), "date": latest["date"]}
    if not dgs2.empty and dgs2["value"].notna().any():
        latest = dgs2.dropna(subset=["value"]).iloc[-1]
        out["two_year"] = {"value": float(latest["value"]), "date": latest["date"]}

    t10y2y = get_series_observations("T10Y2Y", limit=5)
    if not t10y2y.empty and t10y2y["value"].notna().any():
        latest = t10y2y.dropna(subset=["value"]).iloc[-1]
        out["ten_two_spread"] = {"value": float(latest["value"]), "date": latest["date"]}

    be10 = get_series_observations("T10YIE", limit=5)
    if not be10.empty and be10["value"].notna().any():
        latest = be10.dropna(subset=["value"]).iloc[-1]
        out["breakeven_10y"] = {"value": float(latest["value"]), "date": latest["date"]}

    baa = get_series_observations("BAA10Y", limit=5)
    if not baa.empty and baa["value"].notna().any():
        latest = baa.dropna(subset=["value"]).iloc[-1]
        out["baa_spread"] = {"value": float(latest["value"]), "date": latest["date"]}

    walcl = get_series_observations("WALCL", limit=5)
    if not walcl.empty and walcl["value"].notna().any():
        latest = walcl.dropna(subset=["value"]).iloc[-1]
        out["fed_balance_sheet"] = {"value": round(float(latest["value"]) / 1e6, 2),
                                     "date": latest["date"]}

    return out


def _fetch_search_series(query: str, limit: int = 25) -> pd.DataFrame:
    """Pure: FRED HTTP → pandas → return. No caching, no Streamlit."""
    data = _get_json(f"{FRED_BASE}/series/search", {
        "search_text": query, "limit": str(limit),
    })
    if data is None:
        return pd.DataFrame()
    series = data.get("seriess", [])
    if not series:
        return pd.DataFrame()
    return pd.DataFrame([{
        "id": s.get("id", ""),
        "title": s.get("title", ""),
        "units": s.get("units", ""),
        "frequency": s.get("frequency", ""),
    } for s in series])


# ═══════════════════════════════════════════════════════════════
# CACHE ADAPTERS — thin @st.cache_data wrappers
# ═══════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def get_series_observations(
    series_id: str,
    observation_start: Optional[str] = None,
    observation_end: Optional[str] = None,
    sort_order: str = "desc",
    limit: int = 1000,
) -> pd.DataFrame:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_series_observations(series_id, observation_start,
                                      observation_end, sort_order, limit)


@st.cache_data(ttl=86400, show_spinner=False)
def get_series_info(series_id: str) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_series_info(series_id)


@st.cache_data(ttl=3600, show_spinner=False)
def get_yield_curve() -> pd.DataFrame:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_yield_curve()


@st.cache_data(ttl=3600, show_spinner=False)
def get_macro_snapshot() -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_macro_snapshot()


@st.cache_data(ttl=3600, show_spinner=False)
def search_series(query: str, limit: int = 25) -> pd.DataFrame:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_search_series(query, limit)

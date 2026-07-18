"""
OHLC candles — yfinance ^GSPC, last N trading days
=====================================================

Public API
----------
``get_volatility_candles(symbol, days)``
    Return ``(DataFrame(t, open, high, low, close, up), source)``.

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Pure fetch (``_fetch_volatility_candles``) — no Streamlit dependency.
Cache adapter (``get_volatility_candles``) — thin ``@st.cache_data`` wrapper.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf


# ═══════════════════════════════════════════════════════════════
# PURE FETCH — no Streamlit dependency, importable anywhere
# ═══════════════════════════════════════════════════════════════
def _fetch_volatility_candles(symbol: str = "^GSPC", days: int = 30):
    """Pure: yfinance → pandas → return. No caching, no Streamlit.

    Falls back to a deterministic placeholder if yfinance errors out.
    """
    df = _try_yf_history(symbol, days=days + 5, interval="1d")
    if df is None:
        return _placeholder_candles(days), "demo"
    df = df.tail(days).reset_index(drop=True).rename(columns={"Date": "t"})
    df = pd.DataFrame({
        "t": pd.to_datetime(df["t"]),
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low":  df["Low"].astype(float),
        "close": df["Close"].astype(float),
    })
    df["up"] = df["close"] >= df["open"]
    return df, "yfinance"


# ═══════════════════════════════════════════════════════════════
# CACHE ADAPTER — thin @st.cache_data wrapper
# ═══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def get_volatility_candles(symbol: str = "^GSPC", days: int = 30):
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_volatility_candles(symbol, days)


# ── internal helpers ────────────────────────────────────────────────── #


def _try_yf_history(_symbol: str, days: int = 90, interval: str = "1d"):
    """Try to fetch daily history from yfinance. Returns DataFrame or None."""
    try:
        hist = yf.Ticker(_symbol).history(period=f"{days}d", interval=interval)
        if hist is None or hist.empty:
            return None
        return hist.reset_index()
    except Exception:
        return None


def _placeholder_candles(days: int = 30) -> pd.DataFrame:
    """Deterministic fallback preset — matches volatility-chart.tsx algorithm."""
    base = 5800.0
    out = []
    now = pd.Timestamp.utcnow().normalize()
    for i in range(days):
        seed = (i * 9301 + 49297) % 233280
        r = seed / 233280.0
        amp = base * 0.012
        center = base + np.sin(i / 4) * amp + (r - 0.5) * amp * 1.6
        opn = center + (r - 0.5) * amp * 0.4
        cls = center + np.sin(i / 3 + 0.2) * amp * 0.9 + (r - 0.45) * amp * 0.5
        hi = max(opn, cls) + amp * 0.3 + r * amp * 0.4
        lo = min(opn, cls) - amp * 0.3 - r * amp * 0.4
        out.append({"t": now - pd.Timedelta(days=(days - i)),
                    "open": opn, "high": hi, "low": lo, "close": cls,
                    "up": cls >= opn})
    return pd.DataFrame(out)

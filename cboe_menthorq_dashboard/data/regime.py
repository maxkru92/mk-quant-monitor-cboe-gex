"""
Regime detection — Cartesian 3-state (trend × vol) classifier
===============================================================

Public API
----------
``get_index_history(symbol, days)``
    Raw daily close history from yfinance. Returns ``DataFrame(t, close)`` or None.
``get_regime_data()``
    Compute regime from real ^GSPC history. Falls back to SPA-mode when yfinance fails.

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Pure fetch (``_fetch_index_history``) — no Streamlit dependency.
Cache adapter (``get_index_history``) — thin ``@st.cache_data`` wrapper.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from cboe_menthorq_dashboard.data.candles import _try_yf_history


# ═══════════════════════════════════════════════════════════════
# PURE FETCH — no Streamlit dependency, importable anywhere
# ═══════════════════════════════════════════════════════════════
def _fetch_index_history(symbol: str = "^GSPC", days: int = 95):
    """Pure: yfinance → pandas → return. No caching, no Streamlit.

    NOTE: ``symbol`` is intentionally NOT underscore-prefixed so Streamlit
    hashes it into the cache key — otherwise AAPL/BRK.A tabs would serve
    the cached ^GSPC bars from the first call.
    """
    hist = _try_yf_history(symbol, days=days, interval="1d")
    if hist is None:
        return None
    hist = hist.tail(days).reset_index(drop=True).rename(columns={"Date": "t"})
    return pd.DataFrame({"t": pd.to_datetime(hist["t"]),
                         "close": hist["Close"].astype(float)})


# ═══════════════════════════════════════════════════════════════
# CACHE ADAPTER — thin @st.cache_data wrapper
# ═══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def get_index_history(symbol: str = "^GSPC", days: int = 95):
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_index_history(symbol, days)


# ── regime classifier ───────────────────────────────────────────────── #


def _classify_regimes(closes: np.ndarray, returns: np.ndarray) -> dict:
    """Cartesian trend × vol classifier. Three macro states:

      0 = Bull / Low Vol  (20d return > +5% AND 20d vol ≤ p60-of-self)
      1 = Sideways        (everything else)
      2 = Bear / High Vol (20d return < −5% AND 20d vol > p60-of-self)
    """
    # returns is len(closes) - 1 (np.diff reduces by 1)
    # All rolling windows are computed from returns, so they share its length.
    n = len(returns)
    macro = np.full(n, 1, dtype=int)  # default sideways
    if n < 25:
        return {"macros": macro.tolist(), "trans": [[0.7,0.2,0.1],[0.2,0.6,0.2],[0.1,0.2,0.7]],
                "probs": [("Bull",0.5,0),("Sideways",0.3,1),("Bear",0.2,2)]}

    # Compute 20d rolling realised vol (annualised) for the full window
    rets_series = pd.Series(returns)
    rolling_20 = rets_series.rolling(window=20, min_periods=5).std() * np.sqrt(252.0)
    rolling_20 = rolling_20.values  # NaN for first 4 entries

    # 20d rolling annualised return (cumulative)
    rolling_20_ret = (np.exp(rets_series.rolling(window=20, min_periods=5).sum()) - 1.0).values

    # Self-referential vol p60: percentile of all valid rolling_20 values
    valid_vols = rolling_20[~np.isnan(rolling_20)]
    if len(valid_vols) >= 5:
        vol_p60 = float(np.percentile(valid_vols, 60))
    else:
        vol_p60 = 0.15

    for i in range(n):
        if np.isnan(rolling_20[i]) or np.isnan(rolling_20_ret[i]):
            macro[i] = 1
            continue
        ret_20 = rolling_20_ret[i]
        vol_20 = rolling_20[i]
        if ret_20 > 0.05 and vol_20 <= vol_p60:
            macro[i] = 0
        elif ret_20 < -0.05 and vol_20 > vol_p60:
            macro[i] = 2
        else:
            macro[i] = 1

    # Self-transition matrix
    trans = np.zeros((3, 3), dtype=float)
    for k in range(1, n):
        a, b = int(macro[k - 1]), int(macro[k])
        trans[a, b] += 1
    for r in range(3):
        s = trans[r].sum()
        if s > 0:
            trans[r] = trans[r] / s

    # Current regime probability proxy: last 20 days
    tail = macro[-20:] if n >= 20 else macro
    probs = [
        ("Bull",     float((tail == 0).mean()), 0),
        ("Sideways", float((tail == 1).mean()), 1),
        ("Bear",     float((tail == 2).mean()), 2),
    ]
    return {"macros": macro.tolist(),
            "trans": trans.tolist(),
            "probs": probs}


# ── public entry point ─────────────────────────────────────────────── #


def get_regime_data() -> dict:
    """Compute regime from real ^GSPC history. Falls back to SPA-mode when yfinance fails."""
    hist = get_index_history("^GSPC", days=95)
    if hist is None or len(hist) < 25:
        return _regime_fallback()
    closes = hist["close"].values
    returns = np.diff(np.log(closes))
    out = _classify_regimes(closes, returns)
    # ``out["macros"]`` has 1 fewer element than closes (returns = diff of log closes),
    # so ``macro[i]`` corresponds to the regime at ``close[i+1]``.
    out["price_path"] = closes.tolist()
    out["source"] = "yfinance-90d"
    return out


def _regime_fallback() -> dict:
    """SPA-mode fallback when yfinance ^GSPC fails."""
    return {
        "price_path": [],
        "macros": [],
        "trans": [[0.7, 0.2, 0.1],
                  [0.2, 0.6, 0.2],
                  [0.1, 0.2, 0.7]],
        "probs": [("Bull", 0.5, 0), ("Sideways", 0.3, 1), ("Bear", 0.2, 2)],
        "source": "fallback",
    }

"""
Monte Carlo parameters — annualised μ, σ from ^GSPC log-returns
=================================================================

Public API
----------
``get_mc_params(symbol, spot_signature)``
    Annualised μ, σ from last 60 trading days of log-returns.

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Pure fetch (``_fetch_mc_params``) — no Streamlit dependency.
Cache adapter (``get_mc_params``) — thin ``@st.cache_data`` wrapper.
"""

from __future__ import annotations

import numpy as np
import streamlit as st

from cboe_menthorq_dashboard.data.regime import get_index_history


# ═══════════════════════════════════════════════════════════════
# PURE FETCH — no Streamlit dependency, importable anywhere
# ═══════════════════════════════════════════════════════════════
def _fetch_mc_params(symbol: str = "^GSPC", spot_signature: float = 0.0) -> dict:
    """Pure: yfinance → numpy → return. No caching, no Streamlit.

    Calls the CACHED ``get_index_history`` for intra-request cache reuse.
    """
    hist = get_index_history(symbol=symbol, days=95)
    if hist is None or len(hist) < 30:
        return {"mu": 0.08, "sigma": 0.25, "start_value": None,
                "source": "fallback-fixed"}
    closes = hist["close"].values
    returns = np.diff(np.log(closes[-60:]))
    mu = float(np.mean(returns)) * 252.0
    sigma = float(np.std(returns, ddof=1)) * np.sqrt(252.0)
    return {"mu": mu, "sigma": sigma, "start_value": None,
            "source": "yfinance-60d"}


# ═══════════════════════════════════════════════════════════════
# CACHE ADAPTER — thin @st.cache_data wrapper
# ═══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def get_mc_params(symbol: str = "^GSPC", spot_signature: float = 0.0) -> dict:
    """Cached wrapper. Same signature, same return type as before.

    NOTE: NEITHER param is underscore-prefixed so Streamlit hashes both —
    critical for per-ticker cache separation.
    """
    return _fetch_mc_params(symbol, spot_signature)

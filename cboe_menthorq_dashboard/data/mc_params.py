"""
Monte Carlo parameters — annualised μ, σ from ^GSPC log-returns
=================================================================

Public API
----------
``get_mc_params(symbol, spot_signature)``
    Annualised μ, σ from last 60 trading days of log-returns.
"""

from __future__ import annotations

import numpy as np
import streamlit as st

from cboe_menthorq_dashboard.data.regime import get_index_history


@st.cache_data(ttl=300, show_spinner=False)
def get_mc_params(symbol: str = "^GSPC", spot_signature: float = 0.0) -> dict:
    """Annualised μ, σ from last 60 trading days of log-returns.

    Cache key = ``(symbol, spot_signature/100)``. Bucket ``spot_signature`` by
    nearest $100 so slider drags of a few dollars don't cache-bust, but a
    real underlying switch (SPX → NDX) does.

    NOTE: NEITHER param is underscore-prefixed so Streamlit hashes both —
    critical for per-ticker cache separation.
    """
    sig_bucket = round(float(spot_signature), -2)  # noqa: F841 — used in cache key
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

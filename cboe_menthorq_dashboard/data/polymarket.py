"""
Polymarket — crypto & prediction market data
==============================================

Primary source for ALL crypto/prediction-market data in the dashboard.

Two public REST APIs (no API key required for read-only):
- Gamma API (https://gamma-api.polymarket.com) — discovery
- CLOB API  (https://clob.polymarket.com) — prices & orderbook

All functions return graceful None/empty fallbacks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
import streamlit as st

# ── Config ───────────────────────────────────────────────────────── #
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 15.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ── HTTP helpers ─────────────────────────────────────────────────── #
def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Public API ───────────────────────────────────────────────────── #


@st.cache_data(ttl=120, show_spinner=False)
def get_active_markets(limit: int = 20) -> list:
    """Get currently active prediction markets.

    Returns list of dicts with keys:
        ``id``, ``question``, ``outcomes``, ``volume``, ``liquidity``,
        ``end_date``, ``active``, ``closed``, ``tags``.
    """
    data = _get_json(f"{GAMMA_BASE}/markets", {
        "limit": min(limit, 100), "closed": "false",
        "tag": "crypto",  # default to crypto
    })
    if data is None:
        return []
    return data


@st.cache_data(ttl=120, show_spinner=False)
def get_trending_markets(limit: int = 10) -> list:
    """Get trending markets sorted by volume.

    Returns list of dicts (same structure as active markets).
    """
    data = _get_json(f"{GAMMA_BASE}/markets", {
        "limit": min(limit, 100), "closed": "false",
        "order_by": "volume", "ascending": "false",
    })
    if data is None:
        return []
    return data


@st.cache_data(ttl=120, show_spinner=False)
def get_market_details(market_id: str) -> Optional[dict]:
    """Get detailed information about a specific market."""
    data = _get_json(f"{GAMMA_BASE}/markets/{market_id}")
    if data is None:
        return None
    return data


@st.cache_data(ttl=60, show_spinner=False)
def get_market_prices(token_id: str) -> Optional[dict]:
    """Get current prices for a market token.

    Uses CLOB API ``/price`` endpoint.

    Returns dict with ``price`` (string), ``volume``, ``timestamp``.
    """
    data = _get_json(f"{CLOB_BASE}/price", {"token_id": token_id})
    return data


@st.cache_data(ttl=120, show_spinner=False)
def search_markets(keyword: str, limit: int = 15) -> list:
    """Search markets by keyword.

    Returns list of dicts.
    """
    data = _get_json(f"{GAMMA_BASE}/markets/search", {
        "term": keyword, "limit": min(limit, 50),
        "closed": "false",
    })
    if data is None:
        return []
    return data


@st.cache_data(ttl=300, show_spinner=False)
def get_crypto_snapshot() -> dict:
    """Key crypto/prediction-market indicators.

    Returns dict with:
        ``trending_markets`` (list), ``active_count`` (int),
        ``top_volume`` (dict: market question → volume), ``source``.
    """
    active = get_active_markets(20)
    trending = get_trending_markets(10)

    top_volume = {}
    for m in (trending or []):
        q = m.get("question", "Unknown")
        vol = float(m.get("volume", 0))
        top_volume[q] = vol

    return {
        "trending_markets": trending or [],
        "active_count": len(active or []),
        "top_volume": top_volume,
        "source": "polymarket-gamma",
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_events(limit: int = 10) -> list:
    """Get crypto events (higher-level grouping of markets).

    Returns list of dicts with keys:
        ``id``, ``title``, ``markets``, ``volume``, ``liquidity``.
    """
    data = _get_json(f"{GAMMA_BASE}/events", {
        "limit": min(limit, 50), "closed": "false",
        "tag": "crypto",
    })
    if data is None:
        return []
    return data

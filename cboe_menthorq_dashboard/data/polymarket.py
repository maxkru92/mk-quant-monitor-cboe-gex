"""
Polymarket — crypto & prediction market data
==============================================

Primary source for ALL crypto/prediction-market data in the dashboard.

Two public REST APIs (no API key required for read-only):
- Gamma API (https://gamma-api.polymarket.com) — discovery
- CLOB API  (https://clob.polymarket.com) — prices & orderbook

All functions return graceful None/empty fallbacks.

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Pure fetch (``_fetch_*``) — no Streamlit dependency, importable anywhere.
Cache adapter (``get_*``) — thin ``@st.cache_data`` wrapper.
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


# ═══════════════════════════════════════════════════════════════
# PURE FETCH FUNCTIONS — no Streamlit dependency
# ═══════════════════════════════════════════════════════════════

def _fetch_active_markets(limit: int = 20) -> list:
    """Pure: Polymarket HTTP → list. No caching, no Streamlit."""
    data = _get_json(f"{GAMMA_BASE}/markets", {
        "limit": min(limit, 100), "closed": "false",
        "tag": "crypto",
    })
    if data is None:
        return []
    return data


def _fetch_trending_markets(limit: int = 10) -> list:
    """Pure: Polymarket HTTP → list. No caching, no Streamlit."""
    data = _get_json(f"{GAMMA_BASE}/markets", {
        "limit": min(limit, 100), "closed": "false",
        "order_by": "volume", "ascending": "false",
    })
    if data is None:
        return []
    return data


def _fetch_market_details(market_id: str) -> Optional[dict]:
    """Pure: Polymarket HTTP → dict. No caching, no Streamlit."""
    data = _get_json(f"{GAMMA_BASE}/markets/{market_id}")
    if data is None:
        return None
    return data


def _fetch_market_prices(token_id: str) -> Optional[dict]:
    """Pure: CLOB HTTP → dict. No caching, no Streamlit."""
    data = _get_json(f"{CLOB_BASE}/price", {"token_id": token_id})
    return data


def _fetch_search_markets(keyword: str, limit: int = 15) -> list:
    """Pure: Polymarket HTTP → list. No caching, no Streamlit."""
    data = _get_json(f"{GAMMA_BASE}/markets/search", {
        "term": keyword, "limit": min(limit, 50),
        "closed": "false",
    })
    if data is None:
        return []
    return data


def _fetch_crypto_snapshot() -> dict:
    """Pure computation on cached data — calls CACHED get_active_markets
    and get_trending_markets for intra-request cache reuse."""
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


def _fetch_events(limit: int = 10) -> list:
    """Pure: Polymarket HTTP → list. No caching, no Streamlit."""
    data = _get_json(f"{GAMMA_BASE}/events", {
        "limit": min(limit, 50), "closed": "false",
        "tag": "crypto",
    })
    if data is None:
        return []
    return data


# ═══════════════════════════════════════════════════════════════
# CACHE ADAPTERS — thin @st.cache_data wrappers
# ═══════════════════════════════════════════════════════════════

@st.cache_data(ttl=120, show_spinner=False)
def get_active_markets(limit: int = 20) -> list:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_active_markets(limit)


@st.cache_data(ttl=120, show_spinner=False)
def get_trending_markets(limit: int = 10) -> list:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_trending_markets(limit)


@st.cache_data(ttl=120, show_spinner=False)
def get_market_details(market_id: str) -> Optional[dict]:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_market_details(market_id)


@st.cache_data(ttl=60, show_spinner=False)
def get_market_prices(token_id: str) -> Optional[dict]:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_market_prices(token_id)


@st.cache_data(ttl=120, show_spinner=False)
def search_markets(keyword: str, limit: int = 15) -> list:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_search_markets(keyword, limit)


@st.cache_data(ttl=300, show_spinner=False)
def get_crypto_snapshot() -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_crypto_snapshot()


@st.cache_data(ttl=300, show_spinner=False)
def get_events(limit: int = 10) -> list:
    """Cached wrapper. Same signature, same return type as before."""
    return _fetch_events(limit)

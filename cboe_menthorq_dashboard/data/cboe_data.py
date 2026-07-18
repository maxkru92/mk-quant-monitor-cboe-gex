"""
CBOE Options Data — direct HTTP extraction from cboe_mcp MCP server
====================================================================

Primary data source for ALL options-related features in the dashboard.
Extracted from ``cboe_mcp/server.py`` (no MCP protocol — pure HTTP calls).

Provides:
- Rich options chain with Greeks, GEX, expected move
- Ticker info (spot, IV30, OHLC, expirations)
- IV history (IV30/IV60/IV90 annual highs/lows)
- GEX profile by strike with flip levels
- Max pain calculation
- IV skew analysis
- Put/Call ratio

Architecture (2026-07 Candidate 3 — Cache-Seam Decoupling)
----------------------------------------------------------
Every public function is split into two layers:

1. **Pure fetch** — ``async def _fetch_*()`` with NO Streamlit dependency.
   Importable anywhere. Testable with ``httpx.MockTransport``.
2. **Cache adapter** — ``@st.cache_data def get_*()`` thin wrapper.
   Same public API, same return types. Consumers see no change.

The seam between "fetch data" and "cache data" is the ``@st.cache_data``
decorator. Swap the adapter (Redis, disk, memory) without touching fetch code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import asyncio as _asyncio

import httpx
import numpy as np
import pandas as pd
import streamlit as st

# ── Constants ─────────────────────────────────────────────────────── #
CBOE_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
CDN_BASE = "https://cdn.cboe.com/api/global"
EDU_BASE = "https://www.cboe.com/education/tools/trade-optimizer"
INDEX_DIR_URL = f"{CDN_BASE}/us_indices/definitions/all_indices.json"
TICKER_EXCEPTIONS: List[str] = ["NDX", "RUT"]
REQUEST_TIMEOUT = 30.0

# Lazy-loaded index list cache
_INDEX_LIST_CACHE: Optional[List[str]] = None


# ── HTTP helpers ──────────────────────────────────────────────────── #
async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(headers=CBOE_HEADERS, timeout=REQUEST_TIMEOUT) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


async def _get_text(url: str) -> str:
    async with httpx.AsyncClient(headers=CBOE_HEADERS, timeout=REQUEST_TIMEOUT) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


def _spot(details: pd.DataFrame) -> Optional[float]:
    try:
        # CBOE raw keys are lowercase_with_underscore; _fetch_ticker_info
        # remaps them to Title Case.
        if "current_price" in details.index:
            v = details.loc["current_price"]
        elif "Current Price" in details.index:
            v = details.loc["Current Price"]
        else:
            return None
        return float(v.iloc[0] if hasattr(v, "iloc") else v)
    except Exception:
        return None


# ── Index / symbol helpers ────────────────────────────────────────── #
async def _get_index_list() -> List[str]:
    global _INDEX_LIST_CACHE
    if _INDEX_LIST_CACHE is None:
        data = await _get_json(INDEX_DIR_URL)
        df = pd.DataFrame(data)
        _INDEX_LIST_CACHE = df["index_symbol"].tolist() if "index_symbol" in df.columns else []
    return _INDEX_LIST_CACHE


async def _cdn_url(ticker: str, endpoint: str) -> str:
    indexes = await _get_index_list()
    prefix = "_" if (ticker in TICKER_EXCEPTIONS or ticker in indexes) else ""
    return f"{CDN_BASE}/{endpoint}/{prefix}{ticker}.json"


def _format_retry_url(symbol: str) -> str:
    """CBOE uses leading underscore for major indices (same as _cdn_url helper)."""
    indexes_set = TICKER_EXCEPTIONS | {"SPX", "VIX", "NDX", "RUT", "DJX", "OEX", "XEO", "XSP"}
    if symbol.upper().strip() in indexes_set:
        return f"_{symbol.upper().strip()}"
    return symbol.upper().strip()


# ═════════════════════════════════════════════════════════════════════ #
# PURE FETCH FUNCTIONS — no Streamlit dependency                       #
#                                                                      #
# These are the "implementation" layer. They take inputs, call HTTP,    #
# transform data, and return results. NO @st.cache_data decorators.     #
# Importable anywhere. Testable with httpx.MockTransport.               #
# ═════════════════════════════════════════════════════════════════════ #

# ── 1. Ticker Info (pure) ────────────────────────────────────────── #
async def _fetch_ticker_info(symbol: str) -> dict:
    """Delayed quote (bid/ask, OHLC, IV30) + all expiration dates for a CBOE ticker.

    Pure async function — no Streamlit dependency.
    """
    indexes = await _get_index_list()
    api_ticker = "^" + symbol if (symbol in TICKER_EXCEPTIONS or symbol in indexes) else symbol

    # Fetch symbol info
    raw = await _get_json(f"{EDU_BASE}/symbol-info/?symbol={api_ticker}")
    data = pd.Series(raw)
    if not data.get("success", False):
        return {"symbol": symbol, "spot": None, "details": {}, "expirations": [], "iv30": None}

    details_df = (
        pd.DataFrame(pd.Series(data["details"]))
        .transpose()
        .reset_index(drop=True)
    )
    expirations: list = data.get("expirations", [])

    # Map columns
    sec_type = str(details_df.get("security_type", pd.Series([""]))[0]).lower()
    if "stock" in sec_type:
        col_map = {
            "symbol": "Symbol", "current_price": "Current Price",
            "bid": "Bid", "ask": "Ask", "bid_size": "Bid Size",
            "ask_size": "Ask Size", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume",
            "iv30": "IV30", "prev_day_close": "Previous Close",
            "price_change": "Change", "price_change_percent": "Change %",
            "iv30_change": "IV30 Change", "iv30_percent_change": "IV30 Change %",
            "last_trade_time": "Last Trade Time", "exchange_id": "Exchange ID",
            "tick": "Tick", "security_type": "Type",
        }
        wanted = [
            "Symbol", "Type", "Tick", "Bid", "Bid Size", "Ask Size",
            "Ask", "Current Price", "Open", "High", "Low", "Close",
            "Volume", "Previous Close", "Change", "Change %", "IV30",
            "IV30 Change", "IV30 Change %", "Last Trade Time",
        ]
    else:
        col_map = {
            "symbol": "Symbol", "security_type": "Type",
            "current_price": "Current Price", "price_change": "Change",
            "price_change_percent": "Change %", "tick": "Tick",
            "open": "Open", "high": "High", "low": "Low", "close": "Close",
            "prev_day_close": "Previous Close", "iv30": "IV30",
            "iv30_change": "IV30 Change", "iv30_percent_change": "IV30 Change %",
            "last_trade_time": "Last Trade Time",
        }
        wanted = [
            "Symbol", "Type", "Tick", "Current Price", "Open", "High",
            "Low", "Close", "Previous Close", "Change", "Change %",
            "IV30", "IV30 Change", "IV30 Change %", "Last Trade Time",
        ]

    out = details_df.rename(columns=col_map)
    out = (
        pd.DataFrame(out, columns=[c for c in wanted if c in out.columns])
        .set_index("Symbol")
        .dropna(axis=1)
        .transpose()
    )

    spot_val = _spot(out)
    iv30_val = None
    try:
        iv30_row = out.loc["IV30"] if "IV30" in out.index else None
        if iv30_row is not None:
            iv30_val = float(iv30_row.iloc[0])
    except Exception:
        pass

    return {
        "symbol": symbol,
        "spot": spot_val,
        "details": out.to_dict(),
        "expirations": expirations,
        "iv30": iv30_val,
        "source": "cboe",
    }


# ── 2. IV History (pure) ─────────────────────────────────────────── #
async def _fetch_iv_history(symbol: str) -> dict:
    """Annualised IV30/IV60/IV90 and HV30/HV60/HV90 annual highs/lows.

    Pure async function — no Streamlit dependency.
    """
    url = await _cdn_url(symbol, "delayed_quotes/historical_data")
    raw = await _get_json(url)
    df = pd.DataFrame(raw).transpose()
    row = df.iloc[1:2]
    col_map = {
        "annual_high": "1Y High", "annual_low": "1Y Low",
        "hv30_annual_high": "HV30 1Y High", "hv30_annual_low": "HV30 1Y Low",
        "hv60_annual_high": "HV60 1Y High", "hv60_annual_low": "HV60 1Y Low",
        "hv90_annual_high": "HV90 1Y High", "hv90_annual_low": "HV90 1Y Low",
        "iv30_annual_high": "IV30 1Y High", "iv30_annual_low": "IV30 1Y Low",
        "iv60_annual_high": "IV60 1Y High", "iv60_annual_low": "IV60 1Y Low",
        "iv90_annual_high": "IV90 1Y High", "iv90_annual_low": "IV90 1Y Low",
        "symbol": "Symbol",
    }
    df2 = row.rename(columns=col_map)
    if "Symbol" in df2.columns:
        df2 = df2.set_index("Symbol")
    records = df2.transpose().reset_index().to_dict(orient="records")
    return {"symbol": symbol, "iv_history": records}


# ── 3. Options Chain (pure) ──────────────────────────────────────── #
async def _fetch_options_chain(symbol: str) -> pd.DataFrame:
    """Full CBOE options chain with Greeks, GEX, DTE, expected move.

    Pure async function — no Streamlit dependency.
    Returns empty DataFrame if the chain is unavailable.
    """
    # Resolve proper URL prefix
    indexes = await _get_index_list()
    api_ticker = "^" + symbol if (symbol in TICKER_EXCEPTIONS or symbol in indexes) else symbol
    raw_info = await _get_json(f"{EDU_BASE}/symbol-info/?symbol={api_ticker}")
    info_data = pd.Series(raw_info)
    details_df = pd.DataFrame(pd.Series(info_data["details"]))
    spot_val = _spot(details_df) or 0.0

    # Fetch options data
    url = await _cdn_url(symbol, "delayed_quotes/options")
    raw = await _get_json(url)
    data = pd.DataFrame(raw["data"])
    opts = pd.Series(data.options.tolist(), index=data.index)

    options_df = pd.DataFrame(list(opts), columns=list(opts.iloc[0].keys()))
    options_df = options_df.rename(
        columns={
            "option": "Option Symbol", "bid": "Bid", "bid_size": "Bid Size",
            "ask": "Ask", "ask_size": "Ask Size", "iv": "IV",
            "open_interest": "OI", "volume": "Vol", "delta": "Delta",
            "gamma": "Gamma", "theta": "Theta", "rho": "Rho", "vega": "Vega",
            "theo": "Theoretical", "change": "Change", "open": "Open",
            "high": "High", "low": "Low", "tick": "Tick",
            "last_trade_price": "Last Price", "last_trade_time": "Timestamp",
            "percent_change": "% Change", "prev_day_close": "Prev Close",
        }
    )
    options_df = options_df.set_index("Option Symbol")

    # Parse option symbol
    idx = (
        pd.Series(options_df.index)
        .str.extractall(
            r"^(?P<Ticker>\D*)(?P<Expiration>\d*)(?P<Type>\D*)(?P<Strike>\d*)"
        )
        .reset_index()
        .drop(columns=["match", "level_0"])
    )
    idx["Expiration"] = pd.DatetimeIndex(idx["Expiration"], yearfirst=True)
    idx["Type"] = idx["Type"].str.replace("C", "Call").str.replace("P", "Put")
    idx["Strike"] = [s.lstrip("0") for s in idx["Strike"]]
    idx["Strike"] = idx["Strike"].astype(float) * 0.001
    idx = idx.drop(columns=["Ticker"])

    chains = (
        idx.join(options_df.reset_index())
        .drop(columns=["Option Symbol"])
        .set_index(["Expiration", "Strike", "Type"])
    )

    # Fixed columns
    for col in ["OI", "Vol", "Bid Size", "Ask Size"]:
        if col in chains.columns:
            chains[col] = chains[col].fillna(0).astype(int)

    # Add computed columns
    chains = chains.reset_index()

    # DTE
    now = datetime.now()
    chains["DTE"] = chains["Expiration"].apply(
        lambda x: max(0, (pd.Timestamp(x) - pd.Timestamp(now)).days)
    )

    # Call-specific
    call_mask = chains["Type"] == "Call"
    chains["$ to Spot"] = 0.0
    chains["% to Spot"] = 0.0
    chains["Breakeven"] = 0.0
    chains["Delta $"] = 0.0
    chains["GEX"] = 0

    if spot_val and spot_val > 0:
        chains.loc[call_mask, "$ to Spot"] = (
            chains.loc[call_mask, "Strike"] + chains.loc[call_mask, "Ask"] - spot_val
        ).round(2)
        chains.loc[~call_mask, "$ to Spot"] = (
            chains.loc[~call_mask, "Strike"] - chains.loc[~call_mask, "Ask"] - spot_val
        ).round(2)
        chains["% to Spot"] = ((chains["$ to Spot"] / spot_val) * 100).round(4)
        chains.loc[call_mask, "Breakeven"] = (
            chains.loc[call_mask, "Strike"] + chains.loc[call_mask, "Ask"]
        )
        chains.loc[~call_mask, "Breakeven"] = (
            chains.loc[~call_mask, "Strike"] - chains.loc[~call_mask, "Ask"]
        )
        chains.loc[call_mask, "Delta $"] = (
            chains.loc[call_mask, "Delta"] * 100
            * chains.loc[call_mask, "OI"] * spot_val
        ).fillna(0).astype(int)
        chains.loc[~call_mask, "Delta $"] = (
            chains.loc[~call_mask, "Delta"] * 100
            * chains.loc[~call_mask, "OI"] * spot_val * -1
        ).fillna(0).astype(int)
        chains["GEX"] = (
            chains["Gamma"] * 100 * chains["OI"] * spot_val**2 * 0.01
        ).fillna(0).astype(int)
        chains.loc[~call_mask, "GEX"] *= -1

    # Expected Move
    chains["Expected Move"] = (
        chains["Last Price"] * chains["IV"]
        * np.sqrt(chains["DTE"] / 252.0)
    ).round(2)

    chains = chains.set_index(["Expiration", "Strike", "Type"])

    col_order = [
        "DTE", "Tick", "Last Price", "Expected Move", "% Change",
        "Theoretical", "$ to Spot", "% to Spot", "Breakeven",
        "Vol", "OI", "Delta $", "GEX", "IV", "Theta", "Delta",
        "Gamma", "Vega", "Rho", "Open", "High", "Low", "Prev Close",
        "Bid Size", "Bid", "Ask", "Ask Size", "Timestamp",
    ]
    return pd.DataFrame(chains, columns=[c for c in col_order if c in chains.columns])


# ── 4. GEX Profile (pure, calls cached get_options_chain) ────────── #
def _fetch_gex_profile(symbol: str, min_dte: int = 0,
                       max_dte: Optional[int] = None) -> dict:
    """GEX profile by strike with flip levels and regime classification.

    Pure computation on cached data — calls the CACHED ``get_options_chain``
    to respect Streamlit's intra-request cache. No async I/O.
    """
    chain = get_options_chain(symbol)
    if chain.empty:
        return {"symbol": symbol, "total_net_gex": 0,
                "gex_regime": "Unknown", "gex_by_strike": []}

    df = chain.reset_index()
    if min_dte > 0:
        df = df[df["DTE"] >= min_dte]
    if max_dte is not None:
        df = df[df["DTE"] <= max_dte]

    call_gex = (
        df[df["Type"] == "Call"].groupby("Strike")["GEX"].sum().rename("Call GEX")
    )
    put_gex = (
        df[df["Type"] == "Put"].groupby("Strike")["GEX"].sum().rename("Put GEX")
    )
    gex = pd.DataFrame({"Call GEX": call_gex, "Put GEX": put_gex}).fillna(0)
    gex["Net GEX"] = gex["Call GEX"] - gex["Put GEX"]
    gex = gex.sort_index()

    total_gex = int(gex["Net GEX"].sum())
    if gex.empty:
        return {"symbol": symbol, "total_net_gex": 0, "gex_regime": "Unknown",
                "gex_by_strike": []}

    max_s = float(gex["Net GEX"].idxmax())
    min_s = float(gex["Net GEX"].idxmin())
    signs = gex["Net GEX"].apply(lambda x: 1 if x >= 0 else -1)
    flips = [
        float(gex.index[i])
        for i in range(1, len(signs))
        if signs.iloc[i] != signs.iloc[i - 1]
    ]

    return {
        "symbol": symbol,
        "total_net_gex": total_gex,
        "gex_regime": "Positive (Stabilizing)" if total_gex > 0 else "Negative (Amplifying)",
        "max_gex_strike": max_s,
        "min_gex_strike": min_s,
        "gex_flip_strikes": flips[:10],
        "gex_by_strike": gex.reset_index().to_dict(orient="records"),
    }


# ── 5. Max Pain (pure, calls cached get_options_chain) ───────────── #
def _fetch_max_pain(symbol: str, expiration: Optional[str] = None) -> dict:
    """Max Pain strike + table of total open interest value by strike.

    Pure computation on cached data — calls the CACHED ``get_options_chain``
    for intra-request cache reuse. No async I/O.
    """
    chain = get_options_chain(symbol)
    if chain.empty:
        return {"symbol": symbol, "max_pain_strike": None, "pain_table": []}

    df = chain.reset_index()
    df = df[["Expiration", "Strike", "Type", "OI"]].copy()

    if expiration:
        df = df[df["Expiration"] == pd.Timestamp(expiration)]
    else:
        nearest_exp = df["Expiration"].min()
        df = df[df["Expiration"] == nearest_exp]

    if df.empty:
        return {"symbol": symbol, "max_pain_strike": None, "pain_table": []}

    calls = df[df["Type"] == "Call"][["Strike", "OI"]].rename(columns={"OI": "call_oi"})
    puts = df[df["Type"] == "Put"][["Strike", "OI"]].rename(columns={"OI": "put_oi"})

    pain_df = pd.merge(calls, puts, on="Strike", how="outer").fillna(0)
    pain_df = pain_df.sort_values("Strike").reset_index(drop=True)

    strikes = pain_df["Strike"].values
    call_oi = pain_df["call_oi"].values
    put_oi = pain_df["put_oi"].values

    pain_values = []
    for s in strikes:
        call_pain = sum(max(s - k, 0) * oi for k, oi in zip(strikes, call_oi))
        put_pain = sum(max(k - s, 0) * oi for k, oi in zip(strikes, put_oi))
        pain_values.append(call_pain + put_pain)

    pain_df["total_pain"] = pain_values
    max_pain_strike = float(pain_df.loc[pain_df["total_pain"].idxmin(), "Strike"])

    top10 = pain_df.sort_values("total_pain").head(10)[
        ["Strike", "call_oi", "put_oi", "total_pain"]
    ]

    return {
        "symbol": symbol,
        "max_pain_strike": max_pain_strike,
        "expiration": str(df["Expiration"].iloc[0].strftime("%Y-%m-%d")),
        "pain_table": top10.to_dict(orient="records"),
    }


# ── 6. IV Skew (pure, calls cached get_*) ────────────────────────── #
def _fetch_iv_skew(symbol: str) -> dict:
    """IV skew (Put IV − Call IV) by expiration.

    Pure computation on cached data — calls the CACHED ``get_options_chain``
    and ``get_ticker_info``. No async I/O.
    """
    chain = get_options_chain(symbol)
    if chain.empty:
        return {"symbol": symbol, "spot": None, "iv_skew": []}

    info = get_ticker_info(symbol)
    spot = info.get("spot")

    if spot is None:
        return {"symbol": symbol, "spot": None, "iv_skew": []}

    df = chain.reset_index()
    calls = df[df["Type"] == "Call"]
    puts = df[df["Type"] == "Put"]

    # ATM call (strike within ±0.5% of spot)
    atm_calls = calls[
        (calls["Strike"] >= spot * 0.995) & (calls["Strike"] <= spot * 1.005)
    ]
    if not atm_calls.empty:
        atm_grp = atm_calls.groupby("Expiration").apply(
            lambda x: x.loc[(x["Strike"] - spot).abs().idxmin()]
        )[["Strike", "IV"]].rename(columns={"Strike": "Call Strike", "IV": "Call IV"})

    # OTM puts (strike ~97% of spot)
    otm_puts = puts[
        (puts["Strike"] >= spot * 0.94) & (puts["Strike"] <= spot * 1.0)
    ]
    if not otm_puts.empty:
        otm_grp = otm_puts.groupby("Expiration").apply(
            lambda x: x.loc[(x["Strike"] - spot * 0.97).abs().idxmin()]
        )[["Strike", "IV"]].rename(columns={"Strike": "Put Strike", "IV": "Put IV"})

    if atm_calls.empty or otm_puts.empty:
        return {"symbol": symbol, "spot": spot, "iv_skew": []}

    skew_df = atm_grp.join(otm_grp)
    if "Put IV" in skew_df.columns and "Call IV" in skew_df.columns:
        skew_df["IV Skew"] = (skew_df["Put IV"] - skew_df["Call IV"]).round(4)

    skew_df.index = skew_df.index.strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "spot": spot,
        "iv_skew": skew_df.reset_index().to_dict(orient="records"),
    }


# ── 7. Put/Call Ratio (pure, calls cached get_options_chain) ──────── #
def _fetch_put_call_ratio(symbol: str) -> dict:
    """Put/Call Ratio by OI and volume.

    Pure computation on cached data — calls the CACHED ``get_options_chain``
    for intra-request cache reuse. No async I/O.
    """
    chain = get_options_chain(symbol)
    if chain.empty:
        return {"symbol": symbol, "pcr_oi": 0, "pcr_vol": 0, "sentiment": "Unknown"}

    df = chain.reset_index()
    calls = df[df["Type"] == "Call"]
    puts = df[df["Type"] == "Put"]

    call_vol = int(calls["Vol"].sum())
    put_vol = int(puts["Vol"].sum())
    call_oi = int(calls["OI"].sum())
    put_oi = int(puts["OI"].sum())

    pcr_vol = put_vol / call_vol if call_vol else 0.0
    pcr_oi = put_oi / call_oi if call_oi else 0.0

    sentiment = "Neutral"
    if pcr_oi > 1.2:
        sentiment = "Bearish (heavy put buying)"
    elif pcr_oi < 0.8:
        sentiment = "Bullish (heavy call buying)"

    return {
        "symbol": symbol,
        "pcr_oi": round(pcr_oi, 3),
        "pcr_vol": round(pcr_vol, 3),
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_vol": call_vol,
        "put_vol": put_vol,
        "sentiment": sentiment,
    }


# ── 8. ATM IV (pure — no cache needed) ───────────────────────────── #
def get_atm_iv(chain: Optional[pd.DataFrame], spot: float) -> float:
    """IV of the ATM call at the nearest-with-positive-OI expiry.

    Falls back to ``0.30`` when the chain is unusable.
    This function is pure — it only transforms data, no HTTP, no cache needed.
    """
    if chain is None or chain.empty:
        return 0.30

    df = chain.reset_index() if "Strike" in chain.index.names else chain.copy()
    required = {"IV", "OI", "Strike", "Expiration", "Type"}
    if not required.issubset(set(df.columns)):
        return 0.30

    calls = df[(df["Type"] == "Call") & (df["OI"] > 0) & (df["IV"] > 0)]
    if calls.empty:
        return 0.30

    now = pd.Timestamp.utcnow()
    future = calls[pd.to_datetime(calls["Expiration"]) >= now]
    if future.empty:
        return 0.30

    nearest_exp = future["Expiration"].min()
    near = future[future["Expiration"] == nearest_exp].copy()
    if near.empty:
        return 0.30

    near["_dist"] = (near["Strike"] - float(spot)).abs()
    return float(near.loc[near["_dist"].idxmin(), "IV"])


# ═════════════════════════════════════════════════════════════════════ #
# CACHE ADAPTERS — thin @st.cache_data wrappers                        #
#                                                                      #
# These are the SEAM between "fetch data" and "cache data".            #
# Same public API, same return types. Consumers see no change.         #
#                                                                      #
# Swap the adapter (Redis, disk, memory) without touching fetch code.  #
# ═════════════════════════════════════════════════════════════════════ #

@st.cache_data(ttl=300, show_spinner=False)
def get_ticker_info(symbol: str) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _asyncio.run(_fetch_ticker_info(symbol))
    except Exception as e:
        return {"symbol": symbol, "spot": None, "details": {},
                "expirations": [], "iv30": None, "source": "error", "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def get_iv_history(symbol: str) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _asyncio.run(_fetch_iv_history(symbol))
    except Exception as e:
        return {"symbol": symbol, "iv_history": [], "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def get_options_chain(symbol: str) -> pd.DataFrame:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _asyncio.run(_fetch_options_chain(symbol))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_gex_profile(symbol: str, min_dte: int = 0,
                    max_dte: Optional[int] = None) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _fetch_gex_profile(symbol, min_dte, max_dte)
    except Exception:
        return {"symbol": symbol, "total_net_gex": 0,
                "gex_regime": "Unknown", "gex_by_strike": []}


@st.cache_data(ttl=300, show_spinner=False)
def get_max_pain(symbol: str, expiration: Optional[str] = None) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _fetch_max_pain(symbol, expiration)
    except Exception:
        return {"symbol": symbol, "max_pain_strike": None, "pain_table": []}


@st.cache_data(ttl=300, show_spinner=False)
def get_iv_skew(symbol: str) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _fetch_iv_skew(symbol)
    except Exception:
        return {"symbol": symbol, "spot": None, "iv_skew": []}


@st.cache_data(ttl=300, show_spinner=False)
def get_put_call_ratio(symbol: str) -> dict:
    """Cached wrapper. Same signature, same return type as before."""
    try:
        return _fetch_put_call_ratio(symbol)
    except Exception:
        return {"symbol": symbol, "pcr_oi": 0, "pcr_vol": 0, "sentiment": "Unknown"}

"""
Live options data fetcher for the Krupp Capital Quant Dashboard

Primary source: ``cboe_mcp`` MCP server extracted functions (``data.cboe_data``).
Provides rich options chain with Greeks, GEX, DTE, expected move, max pain, IV skew.

Backed by the **CBOE delayed quotes API** — same as the MCP server, but called
directly via ``httpx`` instead of the MCP protocol (works on Streamlit Cloud).
"""

from __future__ import annotations

import logging
import warnings
from typing import Optional

import pandas as pd
import yfinance as yf

from cboe_menthorq_dashboard.data import cboe_data

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #
# Tickers whose prices come from CBOE / US index feeds; Yahoo Finance
# stores them under a caret-prefixed namespace (^SPX, ^NDX, ...).
# LiveOptionsFetcher must use the caret form for the yfinance fallback
# chain or ``yf.Ticker("SPX").history(...)`` returns nothing.
_YF_CARET_TICKERS = frozenset({
    "SPX", "NDX", "RUT", "VIX", "DJX", "OEX", "XEO", "XSP",
})


class LiveOptionsFetcher:
    """Fetch live options data from CBOE via ``data.cboe_data``.

    Provides spot price from CBOE with yfinance fallback, and a rich
    options chain with Greeks, GEX, DTE, expected move, and more.
    """

    def __init__(self, symbol: str):
        raw = (symbol or "").upper().strip()
        self.symbol = raw
        # CBOE understands "SPX" directly (it adds the caret itself for
        # items in its index list); Yahoo Finance wants "^SPX".
        # Keep both — CBOE calls use ``self.symbol``, yfinance uses
        # ``self._yf_symbol``.
        self._yf_symbol = f"^{raw}" if raw in _YF_CARET_TICKERS else raw
        self._ticker = yf.Ticker(self._yf_symbol) if raw else None

    # ------------------------------------------------------------------ #
    # Spot price
    # ------------------------------------------------------------------ #
    def spot_price(self) -> float:
        """Return the last traded price of the underlying.

        Priority:
        1. CBOE ticker info (``data.cboe_data.get_ticker_info``)
        2. Yahoo Finance history
        3. Yahoo Finance fast_info
        4. Yahoo Finance info dict
        """
        # 1. CBOE ticker info (primary, from cboe_mcp extraction)
        try:
            info = cboe_data.get_ticker_info(self.symbol) or {}
            spot = info.get("spot")
            if spot and spot > 0:
                log.debug("CBOE spot price for %s: %.2f", self.symbol, spot)
                return float(spot)
            log.debug("CBOE returned no spot for %s (keys=%s)",
                      self.symbol, sorted(info.keys()))
        except Exception as e:
            log.warning("CBOE spot fetch failed for %s: %s", self.symbol, e)

        # 2–4. Yahoo Finance fallbacks
        if self._ticker is None:
            raise ValueError(f"Could not determine spot price for {self.symbol}")

        try:
            hist = self._ticker.history(period="5d", interval="1d")
            if not hist.empty and "Close" in hist.columns:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            log.debug("yfinance .history failed for %s: %s", self._yf_symbol, e)

        try:
            fast = self._ticker.fast_info
            if fast is not None and getattr(fast, "last_price", None):
                return float(fast.last_price)  # type: ignore[arg-type]
        except Exception as e:
            log.debug("yfinance fast_info failed for %s: %s", self._yf_symbol, e)

        try:
            info = self._ticker.info or {}
            for key in ("regularMarketPrice", "previousClose"):
                if info.get(key) is not None:
                    return float(info[key])
        except Exception as e:
            log.debug("yfinance info failed for %s: %s", self._yf_symbol, e)

        raise ValueError(f"Could not determine spot price for {self.symbol}")

    # ------------------------------------------------------------------ #
    # Options chain from cboe_data (cboe_mcp extraction)
    # ------------------------------------------------------------------ #
    def fetch_chain(self) -> pd.DataFrame:
        """
        Fetch the complete options chain from ``cboe_data.get_options_chain``.

        Returns a DataFrame with columns:
            expiration, strike, type, last_price, bid, ask, volume, open_interest,
            iv, delta, gamma, theta, vega, rho, DTE, OI, Vol, GEX, Delta$, ...

        This is a **rich chain** with Greeks, GEX, DTE, expected move,
        dollar-to-spot, breakeven, and more — all from the cboe_mcp extraction.
        """
        chain = cboe_data.get_options_chain(self.symbol)
        if chain.empty:
            raise ValueError(f"No options data returned by CBOE for {self.symbol}")

        # Remap to the standard schema used downstream (GEXCalculator, etc.)
        chain = chain.reset_index()
        chain = chain.rename(columns={
            "Expiration": "expiration",
            "Strike": "strike",
            "Type": "type",
            "Last Price": "last_price",
            "Bid": "bid",
            "Ask": "ask",
            "Vol": "volume",
            "OI": "open_interest",
            "IV": "iv",
            "Delta": "delta",
            "Gamma": "gamma",
            "Theta": "theta",
            "Vega": "vega",
            "Rho": "rho",
        })

        # Ensure GEXCalculator has the numeric columns it expects
        for col in ["strike", "last_price", "bid", "ask", "iv",
                    "delta", "gamma", "theta", "vega", "rho"]:
            if col in chain.columns:
                chain[col] = pd.to_numeric(chain[col], errors="coerce")

        if "volume" in chain.columns:
            chain["volume"] = chain["volume"].fillna(0).astype(int)
        if "open_interest" in chain.columns:
            chain["open_interest"] = chain["open_interest"].fillna(0).astype(int)

        # Mid price
        chain["mid_price"] = (chain["bid"] + chain["ask"]) / 2
        chain["mid_price"] = chain["mid_price"].fillna(chain["last_price"])

        # Sort by expiration, strike
        if "expiration" in chain.columns:
            chain = chain.sort_values(["expiration", "strike", "type"])

        return chain.reset_index(drop=True)

    def fetch_all_chains(self) -> pd.DataFrame:
        """CBOE returns all expirations in one request, so this is a thin wrapper."""
        return self.fetch_chain()


def fetch_ticker_info(symbol: str) -> dict:
    """Return a dict with spot price, name, and sector info.

    Uses ``cboe_data.get_ticker_info`` (CBOE) as primary source,
    falls back to yfinance."""
    try:
        info = cboe_data.get_ticker_info(symbol)
        spot = info.get("spot")
        if spot and spot > 0:
            return {
                "symbol": symbol.upper(),
                "spot": spot,
                "name": symbol.upper(),
                "currency": "USD",
                "exchange": "CBOE",
            }
    except Exception:
        pass

    # Fallback to yfinance
    fetcher = LiveOptionsFetcher(symbol)
    spot = fetcher.spot_price()
    name = symbol.upper()
    currency = "USD"
    try:
        yf_info = yf.Ticker(symbol).info or {}
        name = yf_info.get("longName") or yf_info.get("shortName") or symbol.upper()
        currency = yf_info.get("currency", "USD")
    except Exception:
        pass
    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "name": name,
        "currency": currency,
        "exchange": "YFINANCE",
    }

"""
Live options data fetcher for the CBOE MenthorQ Dashboard.

Primary source: CBOE delayed quotes API (reliable, free, provides Greeks).
Spot price source: Yahoo Finance (lightweight) with CBOE fallback.
"""

from __future__ import annotations

import re
import time
import warnings
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")


# ------------------------------------------------------------------ #
# Shared session
# ------------------------------------------------------------------ #
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _retry(func, max_retries: int = 3, sleep_base: float = 1.0):
    """Simple retry wrapper with exponential backoff."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(sleep_base * (2 ** attempt))
    raise last_exc


def _format_cboe_symbol(symbol: str) -> str:
    """CBOE uses an leading underscore for major indices."""
    symbol = symbol.upper().strip()
    indexes = {"SPX", "VIX", "NDX", "RUT", "DJX", "OEX", "XEO", "XSP"}
    if symbol in indexes:
        return f"_{symbol}"
    return symbol


# Pre-compiled regex for CBOE option symbols like SPXW250417C05050000
_OPTION_SYMBOL_RE = re.compile(r"(\d{6})([CP])(\d+)$")


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #
class LiveOptionsFetcher:
    """Fetch live options data from CBOE (primary) and Yahoo Finance (spot)."""

    def __init__(self, symbol: str):
        self.symbol = symbol.upper().strip()
        self._ticker = yf.Ticker(self.symbol)

    # ------------------------------------------------------------------ #
    # Spot price
    # ------------------------------------------------------------------ #
    def spot_price(self) -> float:
        """Return the last traded price of the underlying."""
        # Primary: CBOE delayed spot from the options endpoint (reliable for indices)
        try:
            spot = self._cboe_options_spot_price()
            if spot:
                return float(spot)
        except Exception:
            pass

        # Fallback: Yahoo Finance
        try:
            hist = self._ticker.history(period="5d", interval="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass

        try:
            fast = self._ticker.fast_info
            if fast is not None:
                return float(fast.last_price)  # type: ignore
        except Exception:
            pass

        try:
            info = self._ticker.info or {}
            if "regularMarketPrice" in info:
                return float(info["regularMarketPrice"])
            if "previousClose" in info:
                return float(info["previousClose"])
        except Exception:
            pass

        raise ValueError(f"Could not determine spot price for {self.symbol}")

    def _cboe_options_spot_price(self) -> float:
        """Fetch delayed spot price from the CBOE options endpoint."""
        cboe_symbol = _format_cboe_symbol(self.symbol)
        url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{cboe_symbol}.json"

        def _fetch():
            r = _SESSION.get(url, timeout=20)
            r.raise_for_status()
            payload = r.json()
            spot = payload.get("data", {}).get("current_price")
            if spot is None:
                raise ValueError("No current_price in CBOE options payload")
            return float(spot)

        return _retry(_fetch, max_retries=3, sleep_base=1.0)

    # ------------------------------------------------------------------ #
    # Options chain from CBOE
    # ------------------------------------------------------------------ #
    def fetch_chain(self) -> pd.DataFrame:
        """
        Fetch the complete options chain from CBOE delayed quotes.

        Returns a DataFrame with columns:
            expiration, strike, type, last_price, bid, ask, volume, open_interest,
            iv, delta, gamma, theta, vega, rho
        """
        cboe_symbol = _format_cboe_symbol(self.symbol)
        url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{cboe_symbol}.json"

        def _fetch():
            r = _SESSION.get(url, timeout=30)
            r.raise_for_status()
            return r.json()

        data = _retry(_fetch, max_retries=3, sleep_base=1.0)

        if not data or "data" not in data or "options" not in data["data"]:
            raise ValueError(f"No options data returned by CBOE for {self.symbol}")

        options = data["data"]["options"]
        if not options:
            raise ValueError(f"Empty options chain from CBOE for {self.symbol}")

        df = pd.DataFrame(options)

        # Rename columns to a common schema
        column_map = {
            "option": "option_symbol",
            "bid": "bid",
            "bid_size": "bid_size",
            "ask": "ask",
            "ask_size": "ask_size",
            "iv": "iv",
            "open_interest": "open_interest",
            "volume": "volume",
            "delta": "delta",
            "gamma": "gamma",
            "theta": "theta",
            "rho": "rho",
            "vega": "vega",
            "theo": "theoretical",
            "change": "change",
            "open": "open",
            "high": "high",
            "low": "low",
            "tick": "tick",
            "last_trade_price": "last_price",
            "last_trade_time": "timestamp",
            "percent_change": "pct_change",
            "prev_day_close": "prev_close",
        }
        df = df.rename(columns=column_map)

        # Parse option symbol: e.g. SPXW250417C05050000
        # Format: [Ticker][YYMMDD][C/P][Strike*1000]
        def _parse_option_symbol(sym: str):
            sym = str(sym)
            m = _OPTION_SYMBOL_RE.search(sym)
            if not m:
                return pd.Series([pd.NaT, None, None])
            exp_str, opt_type, strike_str = m.groups()
            expiration = pd.to_datetime(exp_str, format="%y%m%d")
            opt_type = "Call" if opt_type == "C" else "Put"
            strike = int(strike_str) / 1000.0
            return pd.Series([expiration, opt_type, strike])

        parsed = df["option_symbol"].apply(_parse_option_symbol)
        df["expiration"] = parsed[0]
        df["type"] = parsed[1]
        df["strike"] = parsed[2]

        # Numeric conversions
        numeric_cols = [
            "strike", "last_price", "bid", "ask", "volume", "open_interest",
            "iv", "delta", "gamma", "theta", "vega", "rho",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["volume"] = df["volume"].fillna(0).astype(int)
        df["open_interest"] = df["open_interest"].fillna(0).astype(int)

        # Mid price
        df["mid_price"] = (df["bid"] + df["ask"]) / 2
        df["mid_price"] = df["mid_price"].fillna(df["last_price"])

        # Select / order columns
        df = df[
            [
                "expiration",
                "strike",
                "type",
                "last_price",
                "bid",
                "ask",
                "mid_price",
                "volume",
                "open_interest",
                "iv",
                "delta",
                "gamma",
                "theta",
                "vega",
                "rho",
            ]
        ]

        return df.reset_index(drop=True)

    def fetch_all_chains(self) -> pd.DataFrame:
        """CBOE returns all expirations in one request, so this is a thin wrapper."""
        df = self.fetch_chain()
        if df.empty:
            raise ValueError(f"No options data available for {self.symbol}")
        return df


def fetch_ticker_info(symbol: str) -> dict:
    """Return a small dict with spot price, name, and sector info."""
    fetcher = LiveOptionsFetcher(symbol)
    spot = fetcher.spot_price()
    info: dict = {}
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        pass
    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "name": info.get("longName") or info.get("shortName") or symbol.upper(),
        "currency": info.get("currency", "USD"),
        "exchange": info.get("exchange", ""),
    }

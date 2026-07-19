"""Tests for LiveOptionsFetcher — symbol normalisation + spot price fallback.

Streamlit Cloud previously crashed on SPX because ``yf.Ticker("SPX")`` is
formally invalid: Yahoo Finance stores index tickers under a caret
namespace (``^SPX``, ``^NDX``, ...). LiveOptionsFetcher must map known
indices to the caret form before constructing the yfinance Ticker.

These tests verify that fix end-to-end without making any live HTTP calls.

Test design note
----------------
Avoid ``patch.object(fetcher._ticker, "fast_info", new=...)`` because yfinance
exposes ``fast_info`` (and ``info``) as class-level descriptors. ``patch.object``
fails to restore them: ``AttributeError: can't delete attribute`` on cleanup.

Instead, replace the entire ``fetcher._ticker`` with a fully-constructed
mock object via ``_mock_ticker()``. Cleaner, no restore errors, no internet.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from cboe_menthorq_dashboard.data import cboe_data
from cboe_menthorq_dashboard.data_fetcher import LiveOptionsFetcher, _YF_CARET_TICKERS


# ------------------------------------------------------------------ #
# Mock helpers
# ------------------------------------------------------------------ #
class _NullFastInfo:
    """Stand-in for a yfinance ``fast_info`` namespace where ``last_price`` is None."""
    last_price = None


def _mock_ticker(history_df=None, fast_info=None, info=None,
                 raise_on_history=False):
    """Build a fully-mocked yf.Ticker-like object with real attributes.

    The constructor ``yf.Ticker(symbol)`` itself makes no HTTP calls
    (yfinance is lazy — HTTP happens on attribute access). We do call
    it in ``LiveOptionsFetcher.__init__``, so to fully isolate these
    tests we replace ``fetcher._ticker`` post-construction.
    """
    class MockTicker:
        pass

    m = MockTicker()
    if raise_on_history:
        def _boom(period, interval):
            raise RuntimeError("history broken")
        m.history = _boom
    else:
        m.history = lambda period, interval: (
            history_df if history_df is not None else pd.DataFrame()
        )
    m.fast_info = fast_info if fast_info is not None else _NullFastInfo()
    m.info = info if info is not None else {}
    return m


def _stub_history(closes=(5940.0, 5945.32)):
    return pd.DataFrame(
        {"Close": list(closes)},
        index=pd.date_range("2026-07-16", periods=len(closes)),
    )


# ------------------------------------------------------------------ #
# Symbol normalisation
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("raw,expected_yf", [
    ("SPX",  "^SPX"),
    ("spx",  "^SPX"),
    ("NDX",  "^NDX"),
    ("VIX",  "^VIX"),
    ("RUT",  "^RUT"),
    ("DJX",  "^DJX"),
    ("XSP",  "^XSP"),
    ("AAPL", "AAPL"),
    ("spy",  "SPY"),
    ("tsla", "TSLA"),
    ("SPY",  "SPY"),
    ("",     ""),
])
def test_constructor_yf_symbol_maps_indices_to_caret(raw, expected_yf):
    """Index tickers → caret; equities → bare; empty → empty.

    Both forms are stored so ``CBOE``-targeted code uses ``self.symbol``
    and ``yfinance``-targeted code uses ``self._yf_symbol``.
    """
    fetcher = LiveOptionsFetcher(raw)
    assert fetcher.symbol == raw.upper().strip()
    assert fetcher._yf_symbol == expected_yf
    if raw.strip():
        assert fetcher._ticker is not None
    else:
        assert fetcher._ticker is None


def test_caret_ticker_set_includes_all_major_indices():
    """Regression guard — every major CBOE index must map to a yfinance
    caret-prefixed symbol. If you add a new index ticker, update the set."""
    must = {"SPX", "NDX", "RUT", "VIX"}
    assert must.issubset(_YF_CARET_TICKERS), (
        f"Missing from caret set: {must - _YF_CARET_TICKERS}"
    )


# ------------------------------------------------------------------ #
# Spot price fallback chain
#   1. CBOE ticker info
#   2. yfinance .history()                 Close column
#   3. yfinance .fast_info.last_price
#   4. yfinance .info[regularMarketPrice]  → .info[previousClose]
# Every level is exercised here without any live HTTP / yfinance call.
# ------------------------------------------------------------------ #
def test_spot_price_uses_cboe_when_available():
    """When CBOE returns a positive spot, that wins immediately (no yfinance)."""
    fetcher = LiveOptionsFetcher("SPX")
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": 5945.32}):
        assert fetcher.spot_price() == pytest.approx(5945.32)


def test_spot_price_falls_back_to_yfinance_history_close():
    """CBOE empty + yfinance .history() populated → uses last Close."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(history_df=_stub_history())
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": None}):
        assert fetcher.spot_price() == pytest.approx(5945.32)


def test_spot_price_falls_back_to_fast_info_last_price():
    """CBOE empty + history empty + fast_info.last_price populated → uses fast_info."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(
        history_df=pd.DataFrame(),
        fast_info=type("F", (), {"last_price": 5946.0})(),
    )
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": None}):
        assert fetcher.spot_price() == pytest.approx(5946.0)


def test_spot_price_uses_regular_market_price_when_only_info_present():
    """CBOE empty + history empty + fast_info null + info.regularMarketPrice → uses it."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(info={"regularMarketPrice": 5947.5})
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": None}):
        assert fetcher.spot_price() == pytest.approx(5947.5)


def test_spot_price_uses_previous_close_when_regular_market_missing():
    """CBOE empty + history empty + fast_info null + info only has previousClose."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(info={"previousClose": 5932.10})
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": None}):
        assert fetcher.spot_price() == pytest.approx(5932.10)


def test_spot_price_falls_back_when_cboe_raises():
    """If CBOE raises (network/rate-limit), exception is caught and falls
    back to yfinance .history() close."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(history_df=_stub_history())
    with patch.object(cboe_data, "get_ticker_info",
                      side_effect=RuntimeError("CBOE blocked")):
        assert fetcher.spot_price() == pytest.approx(5945.32)


def test_spot_price_skips_negative_cboe_spot():
    """CBOE spot <= 0 must not be accepted (defensive against bad upstream data)."""
    fetcher = LiveOptionsFetcher("SPX")
    fetcher._ticker = _mock_ticker(info={"regularMarketPrice": 5948.0})
    # CBOE returns a 0/negative spot — must not be used.
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": 0}):
        assert fetcher.spot_price() == pytest.approx(5948.0)


def test_spot_price_raises_when_all_fallbacks_fail():
    """All four levels blank + CBOE empty → raise ValueError naming the symbol."""
    fetcher = LiveOptionsFetcher("FOOBAR")
    fetcher._ticker = _mock_ticker()  # history empty, fast_info null, info {}
    with patch.object(cboe_data, "get_ticker_info",
                      return_value={"spot": None}):
        with pytest.raises(ValueError, match="FOOBAR"):
            fetcher.spot_price()

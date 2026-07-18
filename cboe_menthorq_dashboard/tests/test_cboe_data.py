"""
Tests for ``data.cboe_data`` — proving Candidate 3 Cache-Seam Decoupling.

These tests import the pure ``_fetch_*`` functions directly — NO Streamlit
runtime required. All HTTP calls are mocked via ``unittest.mock.patch``.

Run with:
    pytest cboe_menthorq_dashboard/tests/test_cboe_data.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════
# Synthetic CBOE test data
# ═══════════════════════════════════════════════════════════════════

MOCK_INDEX_LIST = [
    "SPX", "VIX", "DJX", "RUT", "NDX", "OEX", "XEO", "XSP",
]

FUTURE_EXPIRY = "2026-08-21"  # ~34 DTE
DATE_PREFIX = FUTURE_EXPIRY.replace("-", "")


def _make_symbol_info_response(symbol: str, spot: float = 5945.0) -> dict:
    return {
        "success": True,
        "details": {
            "current_price": str(spot),
            "symbol": symbol,
            "security_type": "index",
            "tick": "0.01",
            "iv30": "15.3",
            "open": "5920.0",
            "high": "5960.0",
            "low": "5910.0",
            "close": "5940.0",
            "prev_day_close": "5935.0",
            "price_change": "5.0",
            "price_change_percent": "0.08",
        },
        "expirations": ["2026-07-18", "2026-07-25", "2026-08-21"],
    }


def _make_options_response(spot: float = 5945.0) -> dict:
    """Synthetic CBOE options JSON with 3 strikes, future expiry for DTE > 0."""
    _dp = DATE_PREFIX  # e.g. "20260821"
    return {
        "data": {
            "options": [
                # --- Calls ---
                {
                    "option": f"SPX{_dp}C05900000",
                    "bid": 125.0, "bid_size": 10,
                    "ask": 127.0, "ask_size": 15,
                    "iv": 0.14, "open_interest": 5000, "volume": 1200,
                    "delta": 0.65, "gamma": 0.0008, "theta": -2.5,
                    "rho": 0.12, "vega": 3.2,
                    "theo": 126.0, "change": 1.5, "percent_change": 1.2,
                    "open": 124.5, "high": 128.0, "low": 123.0, "tick": "0.05",
                    "last_trade_price": 126.0,
                    "last_trade_time": "2026-07-18T14:30:00Z",
                    "prev_day_close": 124.5,
                },
                {
                    "option": f"SPX{_dp}C05945000",
                    "bid": 45.0, "bid_size": 25,
                    "ask": 47.0, "ask_size": 30,
                    "iv": 0.15, "open_interest": 8000, "volume": 2500,
                    "delta": 0.50, "gamma": 0.0012, "theta": -3.5,
                    "rho": 0.08, "vega": 4.0,
                    "theo": 46.0, "change": 0.8, "percent_change": 1.7,
                    "open": 45.2, "high": 47.5, "low": 44.8, "tick": "0.05",
                    "last_trade_price": 46.0,
                    "last_trade_time": "2026-07-18T14:35:00Z",
                    "prev_day_close": 45.2,
                },
                {
                    "option": f"SPX{_dp}C06000000",
                    "bid": 8.0, "bid_size": 50,
                    "ask": 9.0, "ask_size": 40,
                    "iv": 0.16, "open_interest": 3000, "volume": 800,
                    "delta": 0.20, "gamma": 0.0004, "theta": -1.5,
                    "rho": 0.03, "vega": 1.8,
                    "theo": 8.5, "change": -0.2, "percent_change": -2.3,
                    "open": 8.7, "high": 9.2, "low": 8.2, "tick": "0.05",
                    "last_trade_price": 8.5,
                    "last_trade_time": "2026-07-18T14:32:00Z",
                    "prev_day_close": 8.7,
                },
                # --- Puts ---
                {
                    "option": f"SPX{_dp}P05900000",
                    "bid": 40.0, "bid_size": 15,
                    "ask": 42.0, "ask_size": 20,
                    "iv": 0.18, "open_interest": 4000, "volume": 900,
                    "delta": -0.30, "gamma": 0.0006, "theta": -2.0,
                    "rho": -0.05, "vega": 2.5,
                    "theo": 41.0, "change": -0.5, "percent_change": -1.2,
                    "open": 41.5, "high": 42.5, "low": 40.0, "tick": "0.05",
                    "last_trade_price": 41.0,
                    "last_trade_time": "2026-07-18T14:33:00Z",
                    "prev_day_close": 41.5,
                },
                {
                    "option": f"SPX{_dp}P05945000",
                    "bid": 85.0, "bid_size": 12,
                    "ask": 88.0, "ask_size": 18,
                    "iv": 0.17, "open_interest": 6000, "volume": 1500,
                    "delta": -0.50, "gamma": 0.0010, "theta": -3.0,
                    "rho": -0.07, "vega": 3.5,
                    "theo": 86.0, "change": -1.0, "percent_change": -1.1,
                    "open": 87.0, "high": 88.5, "low": 84.5, "tick": "0.05",
                    "last_trade_price": 86.0,
                    "last_trade_time": "2026-07-18T14:34:00Z",
                    "prev_day_close": 87.0,
                },
                {
                    "option": f"SPX{_dp}P06000000",
                    "bid": 135.0, "bid_size": 8,
                    "ask": 138.0, "ask_size": 10,
                    "iv": 0.19, "open_interest": 2500, "volume": 600,
                    "delta": -0.70, "gamma": 0.0005, "theta": -2.8,
                    "rho": -0.10, "vega": 2.0,
                    "theo": 136.0, "change": -1.5, "percent_change": -1.1,
                    "open": 137.5, "high": 139.0, "low": 134.0, "tick": "0.05",
                    "last_trade_price": 136.0,
                    "last_trade_time": "2026-07-18T14:36:00Z",
                    "prev_day_close": 137.5,
                },
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════
# Tests — no Streamlit runtime needed!
# ═══════════════════════════════════════════════════════════════════

class TestFetchOptionsChain:
    """Prove that ``_fetch_options_chain`` is importable and testable
    WITHOUT Streamlit. This is the payoff of Candidate 3."""

    @pytest.mark.asyncio
    async def test_returns_dataframe_with_expected_columns(self):
        """SPX chain must return a non-empty DataFrame with key columns."""
        from cboe_menthorq_dashboard.data import cboe_data

        options_data = _make_options_response(5945.0)
        call_count = [0]

        async def _mock_get_json(url: str) -> dict | None:
            call_count[0] += 1
            if "symbol-info" in url:
                return _make_symbol_info_response("SPX", 5945.0)
            if "delayed_quotes/options" in url:
                return options_data
            return None

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
        ):
            df = await cboe_data._fetch_options_chain("SPX")

        assert not df.empty, "Chain must not be empty for SPX"
        assert call_count[0] == 2, f"Expected 2 HTTP calls, got {call_count[0]}"

        required_cols = {
            "DTE", "Last Price", "IV", "Delta", "Gamma", "Theta", "Vega",
            "Rho", "OI", "Vol", "GEX", "Expected Move", "Bid", "Ask",
        }
        missing = required_cols - set(df.columns)
        assert not missing, f"Missing columns: {missing}"

    @pytest.mark.asyncio
    async def test_gex_signs_are_correct(self):
        """Calls = positive GEX, Puts = negative GEX (per convention)."""
        from cboe_menthorq_dashboard.data import cboe_data

        options_data = _make_options_response(5945.0)

        async def _mock_get_json(url: str) -> dict | None:
            if "symbol-info" in url:
                return _make_symbol_info_response("SPX", 5945.0)
            if "delayed_quotes/options" in url:
                return options_data
            return None

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
        ):
            df = await cboe_data._fetch_options_chain("SPX")

        df_flat = df.reset_index()
        calls = df_flat[df_flat["Type"] == "Call"]
        puts = df_flat[df_flat["Type"] == "Put"]

        assert len(calls) == 3, f"Expected 3 calls, got {len(calls)}"
        assert len(puts) == 3, f"Expected 3 puts, got {len(puts)}"
        assert (calls["GEX"] > 0).all(), "All call GEX must be positive"
        assert (puts["GEX"] < 0).all(), "All put GEX must be negative"

    @pytest.mark.asyncio
    async def test_handles_http_error_gracefully(self):
        """Pure function propagates exceptions — error handling lives in
        the ``get_options_chain`` cache adapter."""
        from cboe_menthorq_dashboard.data import cboe_data

        async def _mock_get_json(_url: str) -> None:
            raise ConnectionError("CBOE unreachable")

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
            pytest.raises(ConnectionError, match="CBOE unreachable"),
        ):
            await cboe_data._fetch_options_chain("SPX")

    @pytest.mark.asyncio
    async def test_dte_is_computed(self):
        """DTE (Days To Expiration) must be a non-negative integer."""
        from cboe_menthorq_dashboard.data import cboe_data

        options_data = _make_options_response(5945.0)

        async def _mock_get_json(url: str) -> dict | None:
            if "symbol-info" in url:
                return _make_symbol_info_response("SPX", 5945.0)
            if "delayed_quotes/options" in url:
                return options_data
            return None

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
        ):
            df = await cboe_data._fetch_options_chain("SPX")

        df_flat = df.reset_index()
        assert "DTE" in df_flat.columns, "DTE column must exist"
        assert (df_flat["DTE"] >= 0).all(), "All DTE values must be >= 0"

    @pytest.mark.asyncio
    async def test_expected_move_is_positive(self):
        """Expected Move = Last Price × IV × √(DTE/252) > 0 for all options."""
        from cboe_menthorq_dashboard.data import cboe_data

        options_data = _make_options_response(5945.0)

        async def _mock_get_json(url: str) -> dict | None:
            if "symbol-info" in url:
                return _make_symbol_info_response("SPX", 5945.0)
            if "delayed_quotes/options" in url:
                return options_data
            return None

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
        ):
            df = await cboe_data._fetch_options_chain("SPX")

        df_flat = df.reset_index()
        em = df_flat["Expected Move"].dropna()
        assert len(em) > 0, "Expected Move should have non-NaN values"
        assert (em > 0).all(), "All Expected Move values must be positive"

    @pytest.mark.asyncio
    async def test_mid_price_is_between_bid_and_ask(self):
        """Last trade price must lie within bid-ask spread."""
        from cboe_menthorq_dashboard.data import cboe_data

        options_data = _make_options_response(5945.0)

        async def _mock_get_json(url: str) -> dict | None:
            if "symbol-info" in url:
                return _make_symbol_info_response("SPX", 5945.0)
            if "delayed_quotes/options" in url:
                return options_data
            return None

        with (
            patch.object(cboe_data, "_get_json", side_effect=_mock_get_json),
            patch.object(cboe_data, "_get_index_list", return_value=MOCK_INDEX_LIST),
        ):
            df = await cboe_data._fetch_options_chain("SPX")

        df_flat = df.reset_index()
        for _, row in df_flat.iterrows():
            bid = float(row["Bid"])
            ask = float(row["Ask"])
            last = float(row["Last Price"])
            assert bid <= last <= ask, (
                f"Last Price {last} outside bid-ask [{bid}, {ask}]"
            )


class TestFetchOptionsChainNoStreamlit:
    """Prove that the pure function imports WITHOUT Streamlit."""

    def test_no_streamlit_import_in_pure_function(self):
        """The _fetch_* functions must not REFERENCE 'streamlit' or 'st' in code."""
        import ast
        from pathlib import Path

        cboe_path = Path("cboe_menthorq_dashboard/data/cboe_data.py")
        source = cboe_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_fetch_"):
                    body_stmts = node.body
                    if body_stmts and isinstance(body_stmts[0], ast.Expr):
                        body_stmts = body_stmts[1:]  # skip docstring
                    for stmt in body_stmts:
                        stmt_source = ast.get_source_segment(source, stmt) or ""
                        assert "streamlit" not in stmt_source.lower(), (
                            f"{node.name}() code references 'streamlit':\n{stmt_source[:200]}"
                        )
                        assert " st." not in stmt_source, (
                            f"{node.name}() code references 'st.':\n{stmt_source[:200]}"
                        )

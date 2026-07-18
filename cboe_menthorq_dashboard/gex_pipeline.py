"""
GEX Pipeline — single entry point for options data processing
==============================================================

Replaces the duplicated pipeline that existed in three call sites:

1. ``app.py:load_data()``     — fetch + GEX + levels + MenthorQ
2. ``charts_webhook.py:_render()`` — fetch + GEX + chart render
3. ``tabs/charts.py:render()``     — GEX by strike + chart render

Now all three call ``GEXPipeline.run(symbol) → PipelineResult``.

Architecture (2026-07 Candidate 1 — Shared Pipeline)
-----------------------------------------------------
The ``PipelineResult`` dataclass is the **interface** — callers
destructure only what they need. The ``GEXPipeline.run()`` method hides
fetching, validation, Greek fallback, GEX computation, level extraction,
expected move, and MenthorQ formatting behind a single call.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cboe_menthorq_dashboard.data_fetcher import LiveOptionsFetcher, fetch_ticker_info
from cboe_menthorq_dashboard.greeks import add_greeks_to_chain
from cboe_menthorq_dashboard.gex_calculator import GEXCalculator
from cboe_menthorq_dashboard.menthorq_formatter import MenthorQString


@dataclass
class PipelineResult:
    """All outputs of the GEX pipeline in one structure.

    Callers destructure only the fields they need:

    - ``app.py`` uses all fields
    - ``charts_webhook.py`` uses ``spot``, ``by_strike``
    - ``tabs/charts.py`` uses ``by_strike`` (optional, backward-compat)
    """
    info: dict
    spot: float
    chain: pd.DataFrame           # chain with GEX column (calculate_gex output)
    by_strike: pd.DataFrame       # GEX aggregated by strike (gex_by_strike output)
    levels: dict
    levels_0dte: dict
    min_1d: float
    max_1d: float
    menthorq_string: str


class GEXPipeline:
    """Single entry point for the "fetch → compute GEX → format" pipeline.

    Usage::

        result = GEXPipeline.run("SPX")
        print(result.menthorq_string)
        chart_bytes = render_chart("SPX", result.by_strike, result.spot)
    """

    @staticmethod
    def run(
        symbol: str,
        risk_free_rate: float = 0.045,
        dividend_yield: float = 0.0,
    ) -> PipelineResult:
        """Execute the full options pipeline for one symbol.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. ``"SPX"``, ``"SPY"``, ``"AAPL"``).
        risk_free_rate : float
            Annual risk-free rate (default 4.5 %).
        dividend_yield : float
            Annual continuous dividend yield (default 0.0).

        Returns
        -------
        PipelineResult
            Dataclass with info, spot, chain, by_strike, levels,
            levels_0dte, min_1d, max_1d, and menthorq_string.

        Raises
        ------
        ValueError
            If the CBOE chain is missing required columns or the
            symbol cannot be resolved.
        """
        # ── 1. Fetch ──────────────────────────────────────────
        fetcher = LiveOptionsFetcher(symbol)
        info = fetch_ticker_info(symbol)
        spot = info["spot"]
        chain = fetcher.fetch_all_chains()

        # ── 2. Validate ───────────────────────────────────────
        required_cols = {"strike", "type", "open_interest", "gamma"}
        missing = required_cols - set(chain.columns)
        if missing:
            raise ValueError(
                f"Options chain for {symbol} is missing required columns: {missing}"
            )

        # ── 3. Greek fallback (Black-Scholes if CBOE Greeks absent) ──
        greek_cols = ["delta", "gamma", "theta", "vega", "rho"]
        if not all(
            col in chain.columns and chain[col].notna().any() for col in greek_cols
        ):
            chain = add_greeks_to_chain(
                chain,
                spot=spot,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )

        # ── 4. GEX computation ────────────────────────────────
        gex_calc = GEXCalculator(chain, spot)
        chain_gex = gex_calc.calculate_gex()
        by_strike = gex_calc.gex_by_strike(chain_gex)
        levels = gex_calc.levels(chain_gex)
        levels_0dte = gex_calc.levels_0dte(chain_gex)

        # ── 5. Expected move ──────────────────────────────────
        _move, min_1d, max_1d = gex_calc.expected_move_1d()

        # ── 6. MenthorQ string ────────────────────────────────
        mq = MenthorQString(
            symbol=symbol,
            spot=spot,
            levels=levels,
            levels_0dte=levels_0dte,
            min_1d=min_1d,
            max_1d=max_1d,
        )

        return PipelineResult(
            info=info,
            spot=spot,
            chain=chain_gex,
            by_strike=by_strike,
            levels=levels,
            levels_0dte=levels_0dte,
            min_1d=min_1d,
            max_1d=max_1d,
            menthorq_string=mq.build(),
        )

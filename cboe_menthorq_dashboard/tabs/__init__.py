"""One Streamlit tab per module — each ``render(...)`` entrypoint is called from ``app.py``.

Existing tabs (pre-2026-07 architecture review):
- ``quant_metrics`` — Vol surface, OHLC chart, regime detection
- ``strategy_calc`` — Options strategy P&L + integrated Monte Carlo
- ``greeks_calc`` — Black-Scholes-Merton Greeks calculator

New tabs (2026-07 Candidate 2: extracted from inline app.py):
- ``summary`` — Gamma Levels, 0DTE Levels, Top 10 GEX strikes
- ``option_chain`` — Live options chain DataFrame
- ``gex_levels`` — GEX by strike DataFrame
- ``charts`` — Institutional 3-panel GEX chart with fallback

CBOE-MCP integration (2026-07 MCP integration):
- All options data now sourced from ``data.cboe_data`` (extracted from cboe_mcp MCP server)
- Richer chain: Greeks, GEX, DTE, IV history, max pain, IV skew, P/C ratio

New tabs (2026-07 MCP integration):
- ``macro`` — FRED macro dashboard (yield curve, CPI, Fed Funds, unemployment)
- ``crypto`` — Polymarket crypto/prediction market dashboard

``data/`` package (post-Candidate 3 + MCP integration):
- ``data.cboe_data`` — Rich CBOE options data (primary source)
- ``data.fred`` — FRED macro indicators
- ``data.polymarket`` — Polymarket prediction markets
- ``data.vol_surface`` — CBOE-chain IV meshgrid
- ``data.candles`` — yfinance OHLC bars
- ``data.regime`` — Cartesian trend×vol regime classifier
- ``data.mc_params`` — annualised μ, σ from log-returns
"""
from __future__ import annotations

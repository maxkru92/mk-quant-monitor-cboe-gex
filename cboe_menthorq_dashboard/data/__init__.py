"""
``data/`` — live-market data helpers for the Krupp Capital Quant Dashboard
===========================================================================

Every module fetches from **real live APIs** (CBOE, FRED, Polymarket, yfinance).
All public functions return graceful fallbacks when the underlying data source
is unavailable — but in normal operation every chart shows real data.

Modules
-------
``cboe_data``      — Rich CBOE options data (extracted from ``cboe_mcp`` MCP server)
                   • Options chain with Greeks, GEX, DTE, expected move
                   • Ticker info (spot, IV30, expirations)
                   • IV history (IV30/IV60/IV90 annual highs/lows, IV rank)
                   • GEX profile by strike with flip levels
                   • Max pain calculation
                   • IV skew analysis
                   • Put/Call ratio
``fred``           — FRED (Federal Reserve Economic Data) — macro indicators
                   • GDP, CPI, Core PCE, Unemployment, Fed Funds Rate
                   • US Treasury yield curve (1M–30Y)
                   • 10Y-2Y spread, Breakeven inflation, BAA spread
                   • Fed Balance Sheet, Recession Probability
``polymarket``     — Polymarket prediction market data (crypto/finance)
                   • Active & trending markets
                   • Market details & prices
                   • Keyword search
``vol_surface``    — CBOE-chain binning to (K, T) IV meshgrid
``candles``        — OHLC price bars from yfinance (^GSPC default)
``regime``         — Cartesian 3-state trend×vol regime classifier
``mc_params``      — annualised μ, σ from last 60 trading days
"""
from __future__ import annotations

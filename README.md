---
title: MK Quant Monitor
emoji: 📊
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 8501
tags:
  - streamlit
  - quantitative-finance
  - volatility
  - options
  - gex
  - market-monitoring
pinned: false
license: mit
---

# MK Quant Monitor

Quantitative Hedgefonds Risk, Volatility, Options & Market Monitoring Dashboard — Dark Institutional Style.

## Features

- 📈 **Live Market Data** — Yahoo Finance, CoinGecko, CBOE, FRED
- 📉 **Volatility Dashboard** — VIX, VVIX, Term Structure, IV Rank
- 🔮 **Options Forensics** — GEX Calculator, Gamma Flip, Cumulative GEX
- 🛡️ **Crash Monitor** — GEX+ Profile, Zero Gamma, BL Forecast
- 📊 **Integrated Modules** — SPX Flow, Crash Risk, Vol Regime Prediction

## Configuration

Set these environment variables in Space Settings:

- `DEMO_MODE=0` — Enable live data (default: 1)
- `FRED_API_KEY` — For macroeconomic data
- `ALPHA_VANTAGE_API_KEY` — For FX/equity data

## Built by Krupp Capital

Institutional Intelligence for Retail Traders.

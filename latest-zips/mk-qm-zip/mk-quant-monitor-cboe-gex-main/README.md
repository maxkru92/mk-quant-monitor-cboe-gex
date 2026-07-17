# CBOE MenthorQ Dashboard

A professional [Streamlit](https://streamlit.io/) dashboard that fetches **live options data** from CBOE, computes **Gamma Exposure (GEX)**, and outputs a **MenthorQ-style gamma data string** for US equities and indices.

## Features

- 🔴 **Live options data** via CBOE delayed quotes (free, stable, provides Greeks & OI)
- 📊 Black-Scholes Greeks fallback when CBOE Greeks are unavailable
- ⚡ GEX calculation per strike and aggregated levels
- 🎯 MenthorQ output string with:
  - Call Resistance
  - Put Support
  - HVL (High Volume Level)
  - 1D Min / 1D Max expected move
  - 0DTE levels
  - Gamma Wall 0DTE
  - Top 10 GEX strikes
- 📈 Interactive charts and tables

## Requirements

- Python 3.11 (see `runtime.txt`)

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501 in your browser.

## Deploy to Streamlit Cloud

1. Push this repository to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io/).
3. Sign in with GitHub.
4. Click **New app**.
5. Select your repository and branch (`main`).
6. Set **Main file path** to `app.py`.
7. Choose an app URL (e.g. `cboe-menthorq-dashboard`).
8. Click **Deploy**.

The app will install dependencies and start. First deploy may take 2–5 minutes.

## Example tickers

- `SPX`
- `SPY`
- `VIX`
- `AAPL`
- `TSLA`

## Data sources

- **CBOE delayed quotes** (primary): options chains, Greeks, open interest, volume. Data is delayed by ~15 minutes (industry standard for free tier).
- **Yahoo Finance** (fallback): spot price and ticker metadata

## Disclaimer

This dashboard is for educational and research purposes only. It is not financial advice. Options data is subject to delay and accuracy limitations of the underlying free data providers.

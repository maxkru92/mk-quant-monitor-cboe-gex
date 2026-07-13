# CBOE MenthorQ Dashboard

A professional Streamlit dashboard that fetches **live options data**, calculates **Greeks** via Black-Scholes, computes **Gamma Exposure (GEX)**, and outputs a **MenthorQ-style gamma data string**.

## Features

- 🔴 **Live data** via Yahoo Finance (`yfinance`)
- 📊 Black-Scholes Greeks: Delta, Gamma, Theta, Vega, Rho
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

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud

1. Push this folder to a GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io/).
3. Sign in with GitHub.
4. Click **New app**.
5. Select repository `maxkru92/Trading-Suite-Light-HF-Edition`, branch `streamlit-cloud`.
6. Set **Main file path** to `cboe_menthorq_dashboard/app.py`.
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

- **Yahoo Finance** (primary): live spot + options chains
- **CBOE delayed quotes** (fallback, not yet active in UI)

## Disclaimer

This dashboard is for educational and research purposes only. It is not financial advice. Options data is subject to delay and accuracy limitations of the underlying free data providers.

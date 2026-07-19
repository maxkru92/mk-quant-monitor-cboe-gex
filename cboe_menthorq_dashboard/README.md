# Krupp Capital Quant Dashboard - powered by CBOE Data

A professional [Streamlit](https://streamlit.io/) dashboard that fetches **live options data** from CBOE delayed quotes and live index history from Yahoo Finance, computes **Gamma Exposure (GEX)**, **Greeks**, **Black-Scholes scenarios**, **volatility surfaces**, a **3-month regime classifier**, **options strategy payoff diagrams** with auto-scaling strikes, and **Monte-Carlo VaR** — all wired to **real, live data** through a single Python package.

## Features

- 🔴 **CBOE delayed-quotes options chain** (Δ, Γ, Θ, ν, ρ, OI, IV, volume — all live)
- 📊 **Black-Scholes fallback** when CBOE Greeks are missing
- ⚡ **GEX** per strike + aggregated levels (Call Resistance, Put Support, HVL, Gamma Wall, 0DTE)
- 🎯 **MenthorQ-style gamma data string** (call / put / HVL / 1D min-max / 0DTE / wall / top-10 strikes)
- 🌐 **Vol Surface** — 3-D Plotly mesh built from live CBOE chain IVs (15 strikes × 12 expiries)
- 🕯️ **Volatility Chart** — 30-day ^GSPC OHLC candles (real yfinance feed)
- 🧭 **Regime Detection** — Cartesian trend × vol classifier on 90 days of real price history
- 🎲 **Strategy Calculator** — 8 presets (long call/put, bull/bear spreads, iron condor, straddle, strangle, butterfly) with auto-scaling strikes (any ticker price level — SPX @ $5945 → AAPL @ $190 → BRK.A @ $600K all work)
- 📈 **Monte Carlo VaR** — vectorised GBM with **realised μ, σ** from the last 60 trading days of ^GSPC log-returns
- 🧮 **Greeks playground** — Black-Scholes-Merton with default σ seeded from **CBOE chain ATM call IV**
- 🕒 **Live market-clock strip** at the top (browser-side `setInterval` — no per-second Streamlit reruns)
- 📋 MenthorQ download button

## Requirements

- Python 3.11 (see `runtime.txt`)
- `pip install -r requirements.txt`

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501 in your browser.

> **Note**: the entry point is `app.py` inside this folder. If you `cd`-into `cboe_menthorq_dashboard/` first, the absolute-package imports resolve just like they do on Streamlit Cloud.

## Deploy to Streamlit Cloud

1. Push this repository to GitHub (any branch — `main` recommended).
2. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in with GitHub.
3. Click **New app**.
4. Pick the repository and branch (`streamlit-gex-dashboard-telegram-broadcaster`).
5. **Main file path**: `cboe_menthorq_dashboard/app.py`
   - The path is relative to the repository **root**, not the dashboard folder.
6. **App URL**: pick a free slug (e.g. `krupp-capital-quant`).
7. Click **Deploy**.

The first deploy will:
1. Install Python 3.11.4 (from `runtime.txt` at the repository root — Streamlit Cloud only reads it from there)
2. Install dependencies (from `cboe_menthorq_dashboard/requirements.txt`) — 2–5 minutes on cold start
3. Launch `streamlit run cboe_menthorq_dashboard/app.py`

> The `runtime.txt` and `.streamlit/config.toml` files are intentionally mirrored at the repository **root** in addition to any copies inside `cboe_menthorq_dashboard/`, because Streamlit Cloud only reads these two files from the repo root.

No secrets are needed — CBOE delayed quotes and yfinance are unauthenticated free public endpoints.

## Example tickers

- `SPX` — S&P 500 index (full Greek chain, dense 0DTE/1DTE/weekly expiries)
- `SPY` — S&P 500 ETF (most-liquid single-name chain)
- `QQQ` / `NDX` — Nasdaq
- `AAPL`, `TSLA`, `NVDA` — single names
- `VIX` — volatility index


## 🔑 FRED API Key — Setup for Live Macro Data

The **🎯 Macro Risk Monitor** tab pulls live data from FRED (Federal Reserve
Economic Data) using VIX, OFR FSI, HY/IG OAS, EFFR, SOFR, RRP, yield curve
and other series. Without a key, the dashboard renders realistic synthetic
DEMO values so it never crashes.

### Where the key is read from

Resolution order (first non-empty wins):

1. **`st.secrets["FRED_API_KEY"]`** — Streamlit-managed secret storage
2. **`FRED_API_KEY`** OS env var — useful for CI / shell-driven runs
3. `""` (empty) — triggers DEMO fallback throughout the tab

### Local development

The repo ships with a gitignored `cboe_menthorq_dashboard/.streamlit/secrets.toml`:

```toml
FRED_API_KEY = "your-fred-api-key-here"
```

`st.secrets` reads this file automatically when you run
`streamlit run cboe_menthorq_dashboard/app.py` locally.

📥 **Get a free key** at <https://fred.stlouisfed.org/docs/api/api_key.html>.

### 🎯 Streamlit Cloud deployment

**Important**: Streamlit Cloud does **NOT** read the
`.streamlit/secrets.toml` from your repo. You must paste the key into the
Streamlit Cloud Secrets panel yourself:

1. Open <https://share.streamlit.io/>
2. Click your app → **⋮ → Settings → Secrets**
3. Paste the following (no `key =` prefix in this UI):

   ```toml
   FRED_API_KEY = "your-fred-api-key-here"
   ```

4. Click **Save** → the app auto-reboots and `LIVE · FRED` badges appear
   in place of `DEMO` badges across all 7 macro sections.

### What changes when the key is live?

* All KPI strips show real values (VIX ≈14.6, HY OAS ≈3.20%, EFFR ≈4.58%)
* The composite Macro Risk Score (top-left) computes from real data
* Spread panels (CDS, Sovereign, Money-Market) show real FRED territories
* The badge header reads `LIVE · FRED + yfinance` instead of `DEMO`
* Synthetic panels (CDS, Sovereign, MOVE, Options Flow) stay tagged
  `[SYNTH]` — these require paid data feeds; mock approximations only.

## Data sources (real, live)

- **CBOE delayed quotes** (`cboe_data` endpoint) — primary data source for spot price, full options chain, Greeks, IV, OI, volume. Standard ~15-minute delay (industry standard for free tier).
- **Yahoo Finance** (`yfinance`) — daily index history (^GSPC) for OHLC candles, regime classifier and MC μ/σ.
- **Black-Scholes-Merton** — local engine (`greeks.py`) when CBOE Greeks are missing.

## Project layout

```
MK_Quant_Monitor/                ← repo root (Streamlit Cloud looks here for runtime.txt + .streamlit/config.toml)
├── runtime.txt                  ← python-3.11.4   (Streamlit Cloud reads ONLY this copy)
├── .streamlit/config.toml       ← dark theme + server settings  (Streamlit Cloud reads ONLY this copy)
└── cboe_menthorq_dashboard/
    ├── __init__.py              ← package marker (so absolute imports work from any cwd)
    ├── app.py                   ← Streamlit Cloud entry point (Main file path)
    ├── requirements.txt         ← streamlit, plotly, yfinance, scipy, …
    ├── greeks.py                ← Black-Scholes engine
    ├── data_fetcher.py          ← CBOE delayed-quotes fetcher
    ├── gex_calculator.py        ← GEX by-strike math
    ├── menthorq_formatter.py    ← gamma-data string builder
    ├── ui/
    │   ├── theme.py             ← palette tokens + global CSS injection
    │   └── chrome.py            ← header / badge / market-clock JS
    └── tabs/
        ├── _real_data.py        ← CBOE/yfinance live-data layer (with fallbacks)
        ├── quant_metrics.py     ← Vol Surface + OHLC + Regime
        ├── strategy_calc.py     ← P&L payoff + auto-scaling presets
        └── greeks_calc.py       ← BSM scenario playground
```

## Source-badge legend

Every visualisation announces its live-vs-fallback status in a coloured badge:

- 🟢 **LIVE · CBOE …** — pulled from the live CBOE delayed-quotes chain
- 🟢 **LIVE · YFINANCE …** — pulled from yfinance (μ, σ from last 60 d of ^GSPC log-returns; 90d for regime)
- 🟡 **FIXED · …** — deterministic placeholder values (only shown if a network call fails)
- 🟡 **FALLBACK · DEMO …** — deterministic smile / placeholder candles (only shown when CBOE chain is too thin)

## Disclaimer

This dashboard is for educational and research purposes only. It is **not financial advice**. Options data is subject to delay and accuracy limitations of the underlying free data providers (CBOE delayed ~15 minutes, yfinance best-effort).

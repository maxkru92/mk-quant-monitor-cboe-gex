"""
CBOE MenthorQ Dashboard
=======================

A professional Streamlit app that fetches live options data,
calculates Greeks & GEX, and outputs a MenthorQ-style gamma data string.

Run locally with:
    streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# Streamlit Cloud runs this file with cwd set to cboe_menthorq_dashboard/,
# so absolute imports like `cboe_menthorq_dashboard.data_fetcher` fail unless
# the repository root is on sys.path. Add it once, idempotently.
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cboe_menthorq_dashboard.data_fetcher import LiveOptionsFetcher, fetch_ticker_info
from cboe_menthorq_dashboard.greeks import add_greeks_to_chain
from cboe_menthorq_dashboard.gex_calculator import GEXCalculator
from cboe_menthorq_dashboard.menthorq_formatter import MenthorQString
from cboe_menthorq_dashboard.ui.theme import inject_css
from cboe_menthorq_dashboard.ui.chrome import render_market_clock
from cboe_menthorq_dashboard.tabs import quant_metrics, strategy_calc, greeks_calc
from cboe_menthorq_dashboard.tabs import _real_data

warnings.filterwarnings("ignore")


# ------------------------------------------------------------------ #
# Page config
# ------------------------------------------------------------------ #
st.set_page_config(
    page_title="Krupp Capital Quant Dashboard - powered by CBOE Data",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------ #
# Theme — apply once at top of every render
# ------------------------------------------------------------------ #
inject_css()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
@st.cache_data(ttl=300, show_spinner=False)
def load_data(symbol: str, risk_free_rate: float = 0.045, dividend_yield: float = 0.0):
    """Fetch and process all data for a given symbol."""
    fetcher = LiveOptionsFetcher(symbol)
    info = fetch_ticker_info(symbol)
    spot = info["spot"]

    # Fetch options chain (CBOE provides Greeks natively)
    chain = fetcher.fetch_all_chains()

    # Validate required columns for GEX calculation
    required_cols = {"strike", "type", "open_interest", "gamma"}
    missing = required_cols - set(chain.columns)
    if missing:
        raise ValueError(f"Options chain is missing required columns: {missing}")

    # If CBOE Greeks are missing, calculate via Black-Scholes
    greek_cols = ["delta", "gamma", "theta", "vega", "rho"]
    if not all(col in chain.columns and chain[col].notna().any() for col in greek_cols):
        chain = add_greeks_to_chain(
            chain,
            spot=spot,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
        )

    # GEX calculations
    gex_calc = GEXCalculator(chain, spot)
    chain_gex = gex_calc.calculate_gex()
    levels = gex_calc.levels(chain_gex)
    levels_0dte = gex_calc.levels_0dte(chain_gex)

    # 1D expected move
    move, min_1d, max_1d = gex_calc.expected_move_1d()

    # MenthorQ string
    mq = MenthorQString(
        symbol=symbol,
        spot=spot,
        levels=levels,
        levels_0dte=levels_0dte,
        min_1d=min_1d,
        max_1d=max_1d,
    )

    return {
        "info": info,
        "spot": spot,
        "chain": chain_gex,
        "levels": levels,
        "levels_0dte": levels_0dte,
        "min_1d": min_1d,
        "max_1d": max_1d,
        "menthorq_string": mq.build(),
    }


def style_header():
    """Render the app header + live market clock strip (above all tabs)."""
    st.markdown(
        '<div class="main-title">Krupp Capital Quant Dashboard - powered by CBOE Data</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="sub-title">Live options &nbsp;·&nbsp; Greeks &nbsp;·&nbsp; '
        'GEX &nbsp;·&nbsp; Vol surface &nbsp;·&nbsp; Strategy &amp; VaR &nbsp;·&nbsp; '
        'MenthorQ-style gamma levels</div>',
        unsafe_allow_html=True,
    )
    # Live market clock — JS-driven, ticks locally in the browser (no per-sec reruns)
    st.components.v1.html(render_market_clock(), height=44)


# ------------------------------------------------------------------ #
# Main app
# ------------------------------------------------------------------ #
def main():
    style_header()

    # Sidebar
    with st.sidebar:
        st.header("Settings")
        symbol = st.text_input("Ticker", value="SPX", max_chars=10).upper().strip()
        risk_free_rate = st.slider("Risk-free rate (%)", min_value=0.0, max_value=10.0, value=4.5, step=0.1) / 100.0
        dividend_yield = st.slider("Dividend yield (%)", min_value=0.0, max_value=10.0, value=0.0, step=0.1) / 100.0
        refresh = st.button("🔄 Refresh Data")
        if refresh:
            st.cache_data.clear()
            st.rerun()

    if not symbol:
        st.info("Enter a ticker symbol (e.g. SPX, SPY, VIX, AAPL) to begin.")
        return

    # Load CBOE options data (sync — already wrapped in spinner)
    try:
        with st.spinner(f"Fetching live options data for {symbol}..."):
            data = load_data(symbol, risk_free_rate=risk_free_rate, dividend_yield=dividend_yield)
    except Exception as e:
        st.error(f"Could not load data for **{symbol}**. Error: {e}")
        return

    info = data["info"]
    spot = data["spot"]
    chain = data["chain"]
    levels = data["levels"]
    levels_0dte = data["levels_0dte"]
    min_1d = data["min_1d"]
    max_1d = data["max_1d"]
    mq_string = data["menthorq_string"]

    # Top metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Symbol", info["symbol"])
    col2.metric("Spot", f"{spot:,.2f}")
    col3.metric("1D Min", f"{min_1d:,.2f}")
    col4.metric("1D Max", f"{max_1d:,.2f}")
    col5.metric("Currency", info.get("currency", "USD"))

    st.divider()

    # PREWARM yfinance caches (5-min TTL) so the FIRST click into any tab
    # doesn't block 10-30 s on Yahoo's CDN inside the tab render. After this
    # prewarm, every yfinance-backed component is a cache hit.
    with st.spinner("Loading live ^GSPC \u03bc/\u03c3 \u00b7 30d OHLC \u00b7 90d regime (warming 5-min cache)\u2026"):
        _real_data.get_mc_params(spot_signature=float(spot))
        _real_data.get_volatility_candles("^GSPC", 30)
        _real_data.get_regime_data()

    # MenthorQ string output
    st.subheader("📋 MenthorQ Gamma Data String")
    st.code(mq_string, language="text")
    st.download_button(
        label="⬇️ Download MenthorQ String",
        data=mq_string,
        file_name=f"{symbol}_menthorq.txt",
        mime="text/plain",
    )

    st.divider()

    # Tabs  (3 new visual tabs first, then the 4 legacy deep-dive tabs)
    tab_qm, tab_strat, tab_greeks, tab_summary, tab_chain, tab_gex, tab_charts = st.tabs(
        [
            "Quant Metrics",
            "Strategy + Monte Carlo",
            "Greeks",
            "Summary",
            "Options Chain",
            "GEX Levels",
            "Charts",
        ]
    )

    # ----- Quant Metrics (vol surface + vol chart + regime detection) -----
    with tab_qm:
        quant_metrics.render(spot_default=spot, chain=chain)

    # ----- Strategy Calculator + integrated Monte Carlo -----
    with tab_strat:
        strategy_calc.render(spot_default=spot, chain=chain)

    # ----- Greeks Calculator -----
    with tab_greeks:
        greeks_calc.render(spot_default=spot, chain=chain)

    # ------------------------------------------------------------------ #
    # Summary tab
    # ------------------------------------------------------------------ #
    with tab_summary:
        st.subheader("Gamma Levels")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Call Resistance", f"{levels.get('call_resistance', 'N/A'):,.2f}" if isinstance(levels.get('call_resistance'), (int, float)) else "N/A")
        c2.metric("Put Support", f"{levels.get('put_support', 'N/A'):,.2f}" if isinstance(levels.get('put_support'), (int, float)) else "N/A")
        c3.metric("HVL", f"{levels.get('hvl', 'N/A'):,.2f}" if isinstance(levels.get('hvl'), (int, float)) else "N/A")
        c4.metric("Gamma Wall", f"{levels.get('gamma_wall', 'N/A'):,.2f}" if isinstance(levels.get('gamma_wall'), (int, float)) else "N/A")

        if levels_0dte:
            st.subheader("0DTE Levels")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Call Resistance 0DTE", f"{levels_0dte.get('call_resistance', 'N/A'):,.2f}" if isinstance(levels_0dte.get('call_resistance'), (int, float)) else "N/A")
            c2.metric("Put Support 0DTE", f"{levels_0dte.get('put_support', 'N/A'):,.2f}" if isinstance(levels_0dte.get('put_support'), (int, float)) else "N/A")
            c3.metric("HVL 0DTE", f"{levels_0dte.get('hvl', 'N/A'):,.2f}" if isinstance(levels_0dte.get('hvl'), (int, float)) else "N/A")
            c4.metric("Gamma Wall 0DTE", f"{levels_0dte.get('gamma_wall', 'N/A'):,.2f}" if isinstance(levels_0dte.get('gamma_wall'), (int, float)) else "N/A")
        else:
            st.info("No 0DTE options available for this ticker.")

        st.subheader("Top 10 GEX Strikes")
        gex_levels = levels.get("gex_levels", [])
        if gex_levels:
            gex_df = pd.DataFrame(enumerate(gex_levels[:10], start=1), columns=["Rank", "Strike"])
            st.dataframe(gex_df, use_container_width=True, hide_index=True)
        else:
            st.info("No GEX levels calculated.")

    # ------------------------------------------------------------------ #
    # Options Chain tab
    # ------------------------------------------------------------------ #
    with tab_chain:
        st.subheader("Live Options Chain")
        display_cols = [
            "expiration",
            "strike",
            "type",
            "last_price",
            "bid",
            "ask",
            "volume",
            "open_interest",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "gex",
        ]
        st.dataframe(
            chain[[c for c in display_cols if c in chain.columns]],
            use_container_width=True,
            hide_index=True,
        )

    # ------------------------------------------------------------------ #
    # GEX Levels tab
    # ------------------------------------------------------------------ #
    with tab_gex:
        st.subheader("GEX by Strike")
        gex_calc = GEXCalculator(chain, spot)
        by_strike = gex_calc.gex_by_strike()
        st.dataframe(by_strike, use_container_width=True)

    # ------------------------------------------------------------------ #
    # Charts tab
    # ------------------------------------------------------------------ #
    with tab_charts:
        st.subheader("Net GEX by Strike")
        gex_calc = GEXCalculator(chain, spot)
        by_strike = gex_calc.gex_by_strike()
        chart_data = by_strike[["net_gex"]].reset_index()
        st.bar_chart(chart_data, x="strike", y="net_gex", use_container_width=True)

        st.subheader("Open Interest by Strike")
        oi_data = by_strike[["call_oi", "put_oi"]].reset_index()
        oi_data["put_oi"] = -oi_data["put_oi"]
        st.bar_chart(oi_data, x="strike", y=["call_oi", "put_oi"], use_container_width=True)


if __name__ == "__main__":
    main()

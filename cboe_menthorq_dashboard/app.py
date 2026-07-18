"""
CBOE MenthorQ Dashboard
=======================

A professional Streamlit app that fetches live options data,
calculates Greeks & GEX, and outputs a MenthorQ-style gamma data string.

Run locally with:
    streamlit run app.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import streamlit as st

# Streamlit Cloud runs this file with cwd set to cboe_menthorq_dashboard/,
# so absolute imports like `cboe_menthorq_dashboard.data_fetcher` fail unless
# the repository root is on sys.path. Add it once, idempotently.
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cboe_menthorq_dashboard.gex_pipeline import GEXPipeline
from cboe_menthorq_dashboard.ui.theme import inject_css
from cboe_menthorq_dashboard.ui.chrome import render_market_clock
from cboe_menthorq_dashboard.tabs import (
    quant_metrics,
    strategy_calc,
    greeks_calc,
    summary,
    option_chain,
    gex_levels,
    charts,
    macro,
    crypto,
)
from cboe_menthorq_dashboard.data import mc_params, candles, regime
from cboe_menthorq_dashboard.data import cboe_data

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
    """Fetch and process all data for a given symbol — delegates to GEXPipeline."""
    result = GEXPipeline.run(symbol, risk_free_rate, dividend_yield)
    return {
        "info": result.info,
        "spot": result.spot,
        "chain": result.chain,
        "by_strike": result.by_strike,
        "levels": result.levels,
        "levels_0dte": result.levels_0dte,
        "min_1d": result.min_1d,
        "max_1d": result.max_1d,
        "menthorq_string": result.menthorq_string,
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
    # Live market clock — server-side rendered via Python zoneinfo
    # (updates on every Streamlit rerun; no JS needed)
    st.markdown(render_market_clock(), unsafe_allow_html=True)


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

    # PREWARM all data caches so the FIRST click into any tab doesn't
    # block on cold API calls. Every cache has 5-min (or longer) TTL.
    with st.spinner("Loading live ^GSPC \u03bc/\u03c3 \u00b7 30d OHLC \u00b7 90d regime \u00b7 CBOE chain \u00b7 FRED \u00b7 Polymarket (warming caches)\u2026"):
        mc_params.get_mc_params(spot_signature=float(spot))
        candles.get_volatility_candles("^GSPC", 30)
        regime.get_regime_data()
        cboe_data.get_options_chain(symbol)
        cboe_data.get_gex_profile(symbol)
        cboe_data.get_put_call_ratio(symbol)

    # ═══════════════════════════════════════════════════════════════ #
    # MenthorQ string output + TradingView copy + download toolbar
    # ═══════════════════════════════════════════════════════════════ #
    st.subheader("📋 MenthorQ Gamma Data String")

    # Styled terminal-card around the code block
    st.markdown(
        '<div style="background:#0b0f1e;border:1px solid rgba(255,255,255,0.06);'
        'border-radius:12px;padding:2px 14px 14px 14px;margin:0 0 6px 0;">',
        unsafe_allow_html=True,
    )
    st.code(mq_string, language="text")
    st.markdown("</div>", unsafe_allow_html=True)

    # --- Action toolbar: Copy to TradingView + Download ---
    tcol1, tcol2, tcol3 = st.columns([1, 1, 3])

    with tcol1:
        # JS-safe string for clipboard API (escape backticks + ${} interpolation)
        js_safe = mq_string.replace("`", "\\`").replace("${", "\\${")
        copy_btn_id = "mq-copy-btn"
        st.markdown(
            f"""
<style>
    #{copy_btn_id} {{
        width:100%; padding:8px 14px; border:1px solid #00e676; border-radius:8px;
        background:#0b0f1e; color:#00e676; cursor:pointer;
        font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:500;
        letter-spacing:0.04em; transition:all 0.25s ease;
        white-space:nowrap; text-overflow:ellipsis; overflow:hidden;
    }}
    #{copy_btn_id}:hover {{
        background:rgba(0,230,118,0.10) !important;
        box-shadow:0 0 20px rgba(0,230,118,0.12);
    }}
</style>
<button id="{copy_btn_id}"
        onclick="var btn=this; navigator.clipboard.writeText(`{js_safe}`)
            .then(function() {{
                btn.innerHTML='✅ Copied!';
                btn.style.background='rgba(0,230,118,0.12)';
                setTimeout(function() {{
                    btn.innerHTML='📋 Copy to TradingView';
                    btn.style.background='#0b0f1e';
                }}, 2200);
            }})
            .catch(function() {{
                btn.innerHTML='⚠️ Select text & Cmd+C';
                setTimeout(function() {{ btn.innerHTML='📋 Copy to TradingView'; }}, 3000);
            }});">
    📋 Copy to TradingView
</button>
""",
            unsafe_allow_html=True,
        )

    with tcol2:
        st.download_button(
            label="⬇️ Download .txt",
            data=mq_string,
            file_name=f"{symbol}_menthorq.txt",
            mime="text/plain",
        )

    with tcol3:
        st.markdown(
            f'<div style="display:flex;align-items:center;height:44px;'
            f'font-family:JetBrains Mono,monospace;font-size:10px;'
            f'color:rgba(255,255,255,0.30);letter-spacing:0.06em;">'
            f'<span style="color:#00e676;font-size:8px;margin-right:6px;">●</span>'
            f'Copy for TradingView Pine Script indicator. '
            f'Format: Call/Resistance · HVL · 1D Move · Gamma Wall · GEX 1—10</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    st.divider()

    # Tabs  (new tabs: macro + crypto from MCP servers)
    tab_qm, tab_strat, tab_greeks, tab_macro, tab_crypto, tab_summary, tab_chain, tab_gex, tab_charts = st.tabs(
        [
            "Quant Metrics",
            "Strategy + Monte Carlo",
            "Greeks",
            "Macro",
            "Crypto",
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

    # ----- Macro Dashboard (FRED) -----
    with tab_macro:
        macro.render()

    # ----- Crypto / Prediction Markets (Polymarket) -----
    with tab_crypto:
        crypto.render()

    # ------------------------------------------------------------------ #
    # Summary tab — gamma levels, 0DTE levels, top 10 GEX strikes
    # ------------------------------------------------------------------ #
    with tab_summary:
        summary.render(levels=levels, levels_0dte=levels_0dte)

    # ------------------------------------------------------------------ #
    # Options Chain tab
    # ------------------------------------------------------------------ #
    with tab_chain:
        option_chain.render(chain=chain)

    # ------------------------------------------------------------------ #
    # GEX Levels tab
    # ------------------------------------------------------------------ #
    with tab_gex:
        gex_levels.render(chain=chain, spot=spot)

    # ------------------------------------------------------------------ #
    # Charts tab — institutional 3-panel GEX chart with fallback
    # ------------------------------------------------------------------ #
    with tab_charts:
        charts.render(chain=chain, spot=spot, symbol=symbol, by_strike=data.get("by_strike"))


if __name__ == "__main__":
    main()

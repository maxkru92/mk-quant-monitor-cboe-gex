"""
Charts tab — institutional 3-panel GEX chart with fallback
============================================================"""

from __future__ import annotations

import datetime as dt

import pytz
import streamlit as st

from cboe_menthorq_dashboard.gex_calculator import GEXCalculator
from cboe_menthorq_dashboard.chart_generator import render_chart


def render(chain, spot: float, symbol: str) -> None:
    """Render the Charts tab with institutional 3-panel GEX chart.

    Falls back to basic ``st.bar_chart`` when the institutional renderer
    fails (e.g. missing optional dependencies like matplotlib/Pillow).

    Parameters
    ----------
    chain : pd.DataFrame
        Options chain with GEX column (output of ``calculate_gex()``).
    spot : float
        Current spot price.
    symbol : str
        Ticker symbol (e.g. ``"SPX"``, ``"SPY"``).
    """
    st.subheader("Institutional GEX Profile")
    gex_calc = GEXCalculator(chain, spot)
    by_strike = gex_calc.gex_by_strike()
    try:
        png_bytes = render_chart(
            symbol=symbol,
            by_strike=by_strike,
            spot=spot,
            date_label=dt.datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d %H:%M BST"),
        )
        st.image(png_bytes, width='stretch')
    except Exception as chart_err:
        st.warning(f"Could not render institutional chart: {chart_err}")
        # Fallback to basic bar charts
        chart_data = by_strike[["net_gex"]].reset_index()
        st.bar_chart(chart_data, x="strike", y="net_gex", width='stretch')

        oi_data = by_strike[["call_oi", "put_oi"]].reset_index()
        oi_data["put_oi"] = -oi_data["put_oi"]
        st.bar_chart(oi_data, x="strike", y=["call_oi", "put_oi"], width='stretch')

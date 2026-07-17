"""
Summary tab — Gamma Levels, 0DTE Levels, Top 10 GEX Strikes
============================================================"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render(levels: dict, levels_0dte: dict) -> None:
    """Render the Summary tab with key gamma levels.

    Parameters
    ----------
    levels : dict
        Full-chain gamma levels from ``gex_calculator.GEXCalculator.levels()``.
    levels_0dte : dict
        0DTE gamma levels from ``gex_calculator.GEXCalculator.levels_0dte()``.
    """
    st.subheader("Gamma Levels")
    c1, c2, c3, c4 = st.columns(4)

    def _fmt(val):
        if isinstance(val, (int, float)):
            return f"{val:,.2f}"
        return "N/A"

    c1.metric("Call Resistance", _fmt(levels.get("call_resistance")))
    c2.metric("Put Support", _fmt(levels.get("put_support")))
    c3.metric("HVL", _fmt(levels.get("hvl")))
    c4.metric("Gamma Wall", _fmt(levels.get("gamma_wall")))

    if levels_0dte:
        st.subheader("0DTE Levels")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Call Resistance 0DTE", _fmt(levels_0dte.get("call_resistance")))
        c2.metric("Put Support 0DTE", _fmt(levels_0dte.get("put_support")))
        c3.metric("HVL 0DTE", _fmt(levels_0dte.get("hvl")))
        c4.metric("Gamma Wall 0DTE", _fmt(levels_0dte.get("gamma_wall")))
    else:
        st.info("No 0DTE options available for this ticker.")

    st.subheader("Top 10 GEX Strikes")
    gex_levels = levels.get("gex_levels", [])
    if gex_levels:
        gex_df = pd.DataFrame(enumerate(gex_levels[:10], start=1), columns=["Rank", "Strike"])
        st.dataframe(gex_df, use_container_width=True, hide_index=True)
    else:
        st.info("No GEX levels calculated.")

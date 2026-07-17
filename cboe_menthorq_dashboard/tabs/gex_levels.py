"""
GEX Levels tab — GEX by strike DataFrame
=========================================="""

from __future__ import annotations

import streamlit as st

from cboe_menthorq_dashboard.gex_calculator import GEXCalculator


def render(chain, spot: float) -> None:
    """Render the GEX by Strike tab.

    Parameters
    ----------
    chain : pd.DataFrame
        Options chain with GEX column (output of ``calculate_gex()``).
    spot : float
        Current spot price.
    """
    st.subheader("GEX by Strike")
    gex_calc = GEXCalculator(chain, spot)
    by_strike = gex_calc.gex_by_strike()
    st.dataframe(by_strike, width='stretch')

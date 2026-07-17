"""
Options Chain tab — live options chain DataFrame
================================================="""

from __future__ import annotations

import streamlit as st


def render(chain) -> None:
    """Render the live options chain tab.

    Parameters
    ----------
    chain : pd.DataFrame
        Full options chain with GEX column (output of ``gex_calculator.calculate_gex()``).
    """
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
        width='stretch',
        hide_index=True,
    )

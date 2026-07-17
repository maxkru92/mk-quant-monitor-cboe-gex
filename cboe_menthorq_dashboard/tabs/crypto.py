"""
Crypto / Prediction Markets tab — Polymarket data
==================================================

Real-time prediction market data from Polymarket (no API key required).
Shows:
1. Trending markets by volume
2. Active markets count + top volume
3. Search markets
4. Market details (when selected)
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.data import polymarket
from cboe_menthorq_dashboard.ui.chrome import terminal_header, live_badge


# ── 1. Trending Markets Table ────────────────────────────────────── #
def _render_trending_markets() -> None:
    with st.spinner("Loading trending markets from Polymarket…"):
        trending = polymarket.get_trending_markets(15)

    if not trending:
        st.info("No trending markets available.")
        return

    rows = []
    for m in trending:
        q = m.get("question", "Unknown")
        vol = float(m.get("volume", 0))
        liq = float(m.get("liquidity", 0))
        end = m.get("end_date", "")[:10]
        rows.append({
            "Question": q[:80],
            "Volume $M": round(vol / 1e6, 2),
            "Liquidity $M": round(liq / 1e6, 2),
            "End Date": end,
        })

    df = pd.DataFrame(rows).head(10)
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "Volume $M": st.column_config.NumberColumn(format="$%.2fM"),
            "Liquidity $M": st.column_config.NumberColumn(format="$%.2fM"),
        },
        hide_index=True,
    )


# ── 2. Volume Bar Chart ──────────────────────────────────────────── #
def _render_volume_chart() -> None:
    with st.spinner("Loading market volume data…"):
        snapshot = polymarket.get_crypto_snapshot()

    top_vol = snapshot.get("top_volume", {})
    if not top_vol:
        st.info("No volume data available.")
        return

    sorted_items = sorted(top_vol.items(), key=lambda x: x[1], reverse=True)[:10]
    questions = [q[:50] + ("…" if len(q) > 50 else "") for q, _ in sorted_items]
    volumes = [v / 1e6 for _, v in sorted_items]  # in $M

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=volumes[::-1], y=questions[::-1],
        orientation="h",
        marker=dict(color="#22d3ee", line=dict(color="#0b0f1e", width=0.5)),
        hovertemplate="$%{x:.2f}M<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8), height=320,
        xaxis=dict(
            title=dict(text="Volume ($M)", font=dict(color="rgba(255,255,255,0.55)", size=10)),
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        ),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None, key="crypto_volume")


# ── 3. Search ────────────────────────────────────────────────────── #
def _render_search() -> None:
    st.markdown(
        '<div style="margin-top:12px;"></div>',
        unsafe_allow_html=True,
    )
    search_term = st.text_input(
        "Search Polymarket markets",
        placeholder="e.g. Bitcoin, Fed, election, AI…",
        label_visibility="collapsed",
    )

    if not search_term or len(search_term.strip()) < 2:
        return

    with st.spinner(f"Searching for '{search_term}'…"):
        results = polymarket.search_markets(search_term.strip(), limit=8)

    if not results:
        st.info(f"No active markets found for '{search_term}'.")
        return

    sr = []
    for m in results:
        q = m.get("question", "Unknown")
        outcomes = ", ".join(m.get("outcomes", ["Yes", "No"])[:3])
        vol = float(m.get("volume", 0))
        sr.append({
            "Question": q[:60],
            "Outcomes": outcomes,
            "Volume $M": round(vol / 1e6, 2),
        })

    st.dataframe(pd.DataFrame(sr), use_container_width=True,
                 column_config={"Volume $M": st.column_config.NumberColumn(format="$%.2fM")},
                 hide_index=True)


# ── Entry point ──────────────────────────────────────────────────── #
def render() -> None:
    badge_html = live_badge("LIVE · POLYMARKET API")
    st.markdown(
        terminal_header("krupp · /crypto · Prediction Markets · Polymarket", badge_html),
        unsafe_allow_html=True,
    )

    with st.spinner("Loading Polymarket data…"):
        snapshot = polymarket.get_crypto_snapshot()

    # Top metrics
    col1, col2 = st.columns(2)
    col1.markdown(
        f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:#22d3ee;opacity:0.85;">
    Active Markets</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.3rem;font-weight:600;color:#fff;margin-top:6px;">
    {snapshot.get('active_count', 0)}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">Polymarket · Gamma API</div>
</div>
""",
        unsafe_allow_html=True,
    )
    col2.markdown(
        f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:#fbbf24;opacity:0.85;">
    Data Source</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1rem;font-weight:600;color:#fff;margin-top:6px;">
    Polymarket CLOB</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">
    No API key required · 2-min cache</div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.divider()

    st.markdown(terminal_header("Top Markets by Volume"), unsafe_allow_html=True)
    _render_trending_markets()

    st.divider()

    st.markdown(terminal_header("Volume Distribution"), unsafe_allow_html=True)
    _render_volume_chart()

    st.divider()

    st.markdown(terminal_header("Search Markets"), unsafe_allow_html=True)
    _render_search()

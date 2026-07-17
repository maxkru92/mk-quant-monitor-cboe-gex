"""
Macro Dashboard tab — FRED data
================================

Real-time US macro indicators from the Federal Reserve Economic Data (FRED) API.
All data is fetched live via ``data.fred`` and cached with 1-hour TTL.

Shows:
1. Key macro snapshot cards (Fed Funds, CPI, Unemployment, 10Y, 10Y-2Y)
2. US Treasury yield curve chart
3. Historical Fed Funds Rate chart
4. Historical CPI / Core PCE chart
5. Fed Balance Sheet + Recession Probability
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.data import fred
from cboe_menthorq_dashboard.ui.chrome import terminal_header, live_badge, demo_badge


def _hex_to_rgba(hex_color: str, alpha: float = 0.10) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


# ── 1. Macro Snapshot Cards ──────────────────────────────────────── #
def _render_snapshot_cards(snapshot: dict) -> None:
    cards = [
        ("Fed Funds Rate", snapshot.get("fed_funds"), "#fbbf24", "%"),
        ("Unemployment",   snapshot.get("unemployment"), "#22d3ee", "%"),
        ("CPI YoY",        snapshot.get("cpi_yoy"), "#34d399", "%"),
        ("Core PCE YoY",   snapshot.get("core_pce"), "#a78bfa", "%"),
    ]
    cols = st.columns(4)
    for col, (label, data, color, unit) in zip(cols, cards):
        if data:
            val = f"{data['value']}{unit}"
            date = data["date"]
        else:
            val = "—"
            date = ""
        col.markdown(
            f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{color};opacity:0.85;">
    {label}</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.2rem;font-weight:600;color:#fff;margin-top:6px;">{val}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">{date}</div>
</div>
""",
            unsafe_allow_html=True,
        )

    cards2 = [
        ("10Y Yield",    snapshot.get("ten_year"), "#fbbf24", "%"),
        ("2Y Yield",     snapshot.get("two_year"), "#22d3ee", "%"),
        ("10Y-2Y Spread", snapshot.get("ten_two_spread"), "#fb7185", "pp"),
        ("Breakeven 10Y", snapshot.get("breakeven_10y"), "#34d399", "%"),
    ]
    cols = st.columns(4)
    for col, (label, data, color, unit) in zip(cols, cards2):
        if data:
            val = f"{data['value']}{unit}"
            date = data["date"]
        else:
            val = "—"
            date = ""
        col.markdown(
            f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{color};opacity:0.85;">
    {label}</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.2rem;font-weight:600;color:#fff;margin-top:6px;">{val}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">{date}</div>
</div>
""",
            unsafe_allow_html=True,
        )


# ── 2. Yield Curve ───────────────────────────────────────────────── #
def _render_yield_curve() -> None:
    with st.spinner("Loading yield curve from FRED…"):
        yc = fred.get_yield_curve()

    if yc.empty:
        st.info("Yield curve data unavailable.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=yc["maturity"], y=yc["yield_pct"],
        mode="lines+markers",
        line=dict(color="#22d3ee", width=2.5),
        marker=dict(color="#22d3ee", size=8,
                    line=dict(color="#0b0f1e", width=1.5)),
        fill="tozeroy",
        fillcolor=_hex_to_rgba("#22d3ee", 0.08),
        name="Yield",
        hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8),
        height=280,
        xaxis=dict(
            title=dict(text="Maturity", font=dict(color="rgba(255,255,255,0.55)", size=10)),
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        ),
        yaxis=dict(
            title=dict(text="Yield %", font=dict(color="rgba(255,255,255,0.55)", size=10)),
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
            zerolinecolor="#2a3456",
        ),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None, key="macro_yield_curve")

    # Latest date
    latest_date = yc["date"].iloc[-1] if "date" in yc.columns else ""
    st.markdown(
        f'<div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;'
        f'color:rgba(255,255,255,0.30);text-align:right;">Updated: {latest_date}</div>',
        unsafe_allow_html=True,
    )


# ── 3. Fed Funds Rate History ───────────────────────────────────── #
def _render_fed_funds_history() -> None:
    with st.spinner("Loading Fed Funds history from FRED…"):
        dff = fred.get_series_observations("FEDFUNDS", limit=365 * 5)
    if dff.empty:
        st.info("Fed Funds history unavailable.")
        return
    dff = dff.dropna(subset=["value"]).tail(365 * 3)  # last 3 years

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dff["date"], y=dff["value"],
        mode="lines",
        line=dict(color="#fbbf24", width=2),
        fill="tozeroy",
        fillcolor=_hex_to_rgba("#fbbf24", 0.08),
        name="Fed Funds Rate",
        hovertemplate="%{x|%b %Y}<br>%{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8), height=250,
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0")),
        yaxis=dict(gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                   zerolinecolor="#2a3456"),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None, key="macro_fed_funds")


# ── 4. CPI / Core PCE History ───────────────────────────────────── #
def _render_inflation_history() -> None:
    with st.spinner("Loading inflation data from FRED…"):
        cpi = fred.get_series_observations("CPIAUCSL", limit=365 * 5)
        core_pce = fred.get_series_observations("PCEPILFE", limit=365 * 5)

    fig = go.Figure()

    if not cpi.empty:
        cpi = cpi.dropna(subset=["value"]).tail(365 * 3)
        # Compute YoY
        cpi_yoy = cpi.copy()
        cpi_yoy["value"] = cpi["value"].pct_change(periods=12) * 100
        cpi_yoy = cpi_yoy.dropna()
        fig.add_trace(go.Scatter(
            x=cpi_yoy["date"], y=cpi_yoy["value"],
            mode="lines", line=dict(color="#34d399", width=2),
            name="CPI YoY",
            hovertemplate="%{x|%b %Y}<br>%{y:.2f}%<extra></extra>",
        ))

    if not core_pce.empty:
        core_pce = core_pce.dropna(subset=["value"]).tail(365 * 3)
        pce_yoy = core_pce.copy()
        pce_yoy["value"] = core_pce["value"].pct_change(periods=12) * 100
        pce_yoy = pce_yoy.dropna()
        fig.add_trace(go.Scatter(
            x=pce_yoy["date"], y=pce_yoy["value"],
            mode="lines", line=dict(color="#fb7185", width=2),
            name="Core PCE YoY",
            hovertemplate="%{x|%b %Y}<br>%{y:.2f}%<extra></extra>",
        ))

    fig.add_hline(y=2.0, line=dict(color="rgba(251,191,36,0.5)", width=1, dash="dot"),
                  annotation_text="Fed Target 2%",
                  annotation_font=dict(color="rgba(251,191,36,0.6)", size=9, family="JetBrains Mono"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8), height=250,
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0")),
        yaxis=dict(gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                   zerolinecolor="#2a3456"),
        font=dict(family="JetBrains Mono"),
        legend=dict(font=dict(color="#8090b0", size=9, family="JetBrains Mono"),
                    bgcolor="rgba(0,0,0,0)", orientation="h", y=1.12),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None, key="macro_inflation")


# ── 5. More Indicators ──────────────────────────────────────────── #
def _render_more_indicators(snapshot: dict) -> None:
    # Fed Balance Sheet + BAA Spread + Breakeven
    bsheet = snapshot.get("fed_balance_sheet")
    baa = snapshot.get("baa_spread")

    cols = st.columns(3)
    indicators = [
        ("Fed Balance Sheet",
         f"${bsheet['value']:,.2f}T" if bsheet else "—",
         "#34d399", bsheet["date"] if bsheet else ""),
        ("BAA-10Y Spread",
         f"{baa['value']:.2f}pp" if baa else "—",
         "#fb7185", baa["date"] if baa else ""),
        ("10Y Breakeven",
         f"{snapshot.get('breakeven_10y', {}).get('value', '—')}%",
         "#22d3ee", snapshot.get("breakeven_10y", {}).get("date", "")),
    ]
    for col, (label, val, color, date) in zip(cols, indicators):
        col.markdown(
            f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{color};opacity:0.85;">
    {label}</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.1rem;font-weight:600;color:#fff;margin-top:6px;">{val}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">{date}</div>
</div>
""",
            unsafe_allow_html=True,
        )


# ── Entry point ──────────────────────────────────────────────────── #
def render() -> None:
    available = fred.is_available()
    badge_html = live_badge("LIVE · FRED API") if available else demo_badge("FALLBACK · NO API KEY")
    st.markdown(
        terminal_header("krupp · /macro · US Macro Dashboard", badge_html),
        unsafe_allow_html=True,
    )

    if not available:
        st.warning(
            "FRED API key not configured. Set the ``FRED_API_KEY`` environment variable "
            "in your Streamlit Cloud secrets (or ``.env`` file locally) to enable live macro data. "
            "Get a free key at https://fred.stlouisfed.org/docs/api/fred/"
        )
        return

    with st.spinner("Loading FRED macro snapshot…"):
        snapshot = fred.get_macro_snapshot()

    _render_snapshot_cards(snapshot)
    st.divider()

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.markdown(terminal_header("Yield Curve"), unsafe_allow_html=True)
        _render_yield_curve()

    with right:
        st.markdown(terminal_header("Fed Funds Rate (3Y)"), unsafe_allow_html=True)
        _render_fed_funds_history()

    st.divider()

    st.markdown(terminal_header("Inflation (YoY %) vs Fed 2% Target"), unsafe_allow_html=True)
    _render_inflation_history()

    st.divider()
    _render_more_indicators(snapshot)

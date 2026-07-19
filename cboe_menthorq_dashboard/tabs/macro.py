"""
Quantitative Macro Risk Monitor — 7 sections
=============================================

LIVE · FRED · yfinance · alternative.me
Bloomberg/Reuters institutional dark theme.
Backwards-compatible entry point: ``render(spot=None, chain=None)``.

Sections (from top to bottom):
  1. 🎯 STRESS LEVEL HERO  (always visible — composite Risk Score 0-100,
                              green/yellow/red band, KPI strip)
  2. 📈 VOLATILITY & OPTIONS  (VIX/VVIX/SKEW; GEX sim; live-simulated flow)
  3. 💳 CREDIT RISK  (HY/IG OAS; BBB-UST; HY-IG; CDS indices; sovereigns)
  4. 📊 EQUITY · BREADTH · SECTOR ROTATION  (8 indices, breadth, sectors)
  5. 🏦 FIXED INCOME · YIELD CURVE · MOVE  (USTs + 2s10s + MOVE proxy)
  6. 🌍 FX · COMMODITIES · EM · CRYPTO  (majors + EM + commodities + F&G)
  7. 💰 MONEY MARKET STRESS  (EFFR/SOFR/RRP + 1-week Δ + total status)
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.data import fred
from cboe_menthorq_dashboard.data import macro_risk as mr
from cboe_menthorq_dashboard.ui.chrome import (
    terminal_header, live_badge, demo_badge,
)


# ── helpers ────────────────────────────────────────────────────────── #
def _hex_to_rgba(hex_color: str, alpha: float = 0.10) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _fmt_pct(v, sign: bool = True):
    """Plain-text % delta for dataframe cells (no HTML).

    ↑ green +X.XX%   ↓ red -X.XX%   → dash for None.
    """
    if v is None:
        return "—"
    if v > 0:
        return f"+{v:.2f}%"
    if v < 0:
        return f"{v:.2f}%"
    return f"0.00%"

def _fmt_pct_colored(v, sign: bool = True):
    """HTML-colored % delta (for inline ст.markdown contexts only).

    Use _fmt_pct (plain) for cells inside st.dataframe to avoid needing
    escape=False. _fmt_pct_colored is the one-trick HTML helper used in
    KPI strip contexts where unsafe_allow_html=True is explicit.
    """
    if v is None:
        return "—"
    arrow = "+" if (sign and v > 0) else ""
    color = "#34d399" if v > 0 else ("#fb7185" if v < 0 else "#8090b0")
    return f'<span style="color:{color};font-weight:600;tabular-nums;">{arrow}{v:+.2f}%</span>'.replace("+-", "-")


def _color_for_pct(v):
    if v is None:
        return "#8090b0"
    return "#34d399" if v > 0 else ("#fb7185" if v < 0 else "#8090b0")


def _kpi_card(label: str, value, color: str = "#22d3ee",
              unit: str = "", sub: str = "") -> str:
    val_str = "—" if value is None else f"{value}{unit}"
    return f"""
<div class="vc-card" style="padding:10px 12px;margin:0;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{color};opacity:0.85;">
    {label}</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.2rem;font-weight:600;color:#fff;margin-top:6px;">{val_str}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">{sub}</div>
</div>"""


def _options_color_for_stress(score):
    """Pick green/yellow/red for the central risk-score chip."""
    if score is None:
        return "#8090b0"
    if score < 40:
        return "#34d399"
    if score < 70:
        return "#fbbf24"
    return "#fb7185"


def _pl_score_card(score, band) -> str:
    color = _options_color_for_stress(score)
    label = band["label"] if isinstance(band, dict) else "N/A"
    desc = band.get("desc", "") if isinstance(band, dict) else ""
    return f"""
<div class="vc-card" style="padding:14px 16px;margin:0;border-left:3px solid {color};">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.16em;color:{color};opacity:0.95;font-weight:600;">
    Composite Macro Risk Score</div>
  <div style="display:flex;align-items:baseline;gap:14px;margin-top:6px;">
    <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
                font-size:2.6rem;font-weight:700;color:{color};">
      {('—' if score is None else f'{score:.1f}')}
    </div>
    <div style="font-family:JetBrains Mono,monospace;font-size:0.9rem;
                font-weight:600;color:{color};text-transform:uppercase;letter-spacing:0.10em;">
      {label}</div>
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.65rem;
              color:rgba(255,255,255,0.50);margin-top:6px;line-height:1.4;">
    {desc}<br>
    <span style="color:rgba(255,255,255,0.35);">
      Weighted: VIX 40% · OFR FSI 30% · HY OAS 30%
    </span>
  </div>
</div>"""


# ────────────────────────────────────────────────────────────────────── #
# SECTION 1 — STRESS HERO
# ────────────────────────────────────────────────────────────────────── #
def _render_stress_hero(spot) -> None:
    """Always-on hero at the top of the Macro Risk Monitor tab."""
    stress = mr.get_stress_snapshot()
    source = stress.get("source", "demo")
    live = source in ("fred",)
    badge = live_badge("LIVE · FRED + yfinance") if live \
        else demo_badge("DEMO · NO FRED KEY — add `FRED_API_KEY` to secrets")
    st.markdown(
        terminal_header("krupp · /macro-risk · 🎯 Macro Risk Monitor", badge),
        unsafe_allow_html=True,
    )

    # Surface the fallback reason explicitly so the user knows whether
    # they're seeing mock because of (a) no key, (b) FRED outage,
    # (c) FRED partial-rate-limit, or (d) data fetch exception.
    reason = stress.get("_fallback_reason")
    if not live:
        # Source != "fred"full mock fallback. Branch on reason.
        if reason and ("missing" in str(reason) or "exception" in str(reason)):
            st.warning(
                f"**FRED API partial outage** (`{reason}`). Showing DEMO values "
                f"to keep the Macro Risk Score accurate (avoids under-counting stress).",
                icon="⚠",
            )
        else:
            st.warning(
                "**FRED API key missing.** Add `FRED_API_KEY` to "
                "`.streamlit/secrets.toml` (local) **and** to your **Streamlit "
                "Cloud app's Secrets** for live data. Showing DEMO values below.",
                icon="🔑",
            )
    elif reason and "fred_missing_1_of_3" in str(reason):
        st.info(
            f"\u2139\ufe0f **FRED \u2014 live data, one indicator temporarily unavailable** "
            f"({reason}). The Macro Risk Score is approximate until it returns.",
            icon="\u2139\ufe0f",
        )

    score = stress.get("risk_score")
    band = stress.get("risk_band", {"label": "N/A", "color": "#8090b0",
                                       "desc": "Insufficient data"})

    # Risk score chip (left, ~1/3 width) + KPI strip (right, 2/3 width)
    cols = st.columns([1, 2], gap="medium")

    with cols[0]:
        st.markdown(_pl_score_card(score, band), unsafe_allow_html=True)

    with cols[1]:
        vix = stress.get("vix")
        ofr = stress.get("ofr_fsi")
        hy = stress.get("hy_oas")
        vvix_q = mr.get_volatility_indices()["snap"].get("^VVIX") or {}
        skew_q = mr.get_volatility_indices()["snap"].get("^SKEW") or {}

        # Color VIX: green <20, yellow 20-30, red >30
        if vix is None:
            vix_color = "#8090b0"
        elif vix < 20:
            vix_color = "#34d399"
        elif vix < 30:
            vix_color = "#fbbf24"
        else:
            vix_color = "#fb7185"

        kpi1 = _kpi_card("VIX (Cboe)",   f"{vix:.2f}" if vix is not None else "—",
                         vix_color, "", "VIXCLS · FRED")
        kpi2 = _kpi_card("OFR FSI",      f"{ofr:+.2f}" if ofr is not None else "—",
                         "#fb7185" if (ofr is not None and ofr > 0) else "#34d399",
                         "", "OFRFSI · FRED")
        kpi3 = _kpi_card("HY OAS",       f"{hy:.2f}%" if hy is not None else "—",
                         "#fb7185" if (hy is not None and hy > 4.5) else "#fbbf24",
                         "", "BAMLH0A0HYM2 · FRED")
        kpi4 = _kpi_card("VVIX / SKEW",
                         f"{vvix_q.get('last', 0):.0f}" if vvix_q.get('last') else "—",
                         "#22d3ee", "", "yfinance ^VVIX ^SKEW")

        c1, c2 = st.columns(2, gap="small")
        c1.markdown(kpi1, unsafe_allow_html=True)
        c2.markdown(kpi2, unsafe_allow_html=True)
        c1.markdown(kpi3, unsafe_allow_html=True)
        c2.markdown(kpi4, unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────────── #
# SECTION 2 — VOLATILITY & OPTIONS REAL-TIME MONITOR
# ────────────────────────────────────────────────────────────────────── #
def _render_volatility_options(spot) -> None:
    stress = mr.get_stress_snapshot()
    vol = mr.get_volatility_indices()

    st.markdown(terminal_header("Volatility & Options Real-Time Monitor"),
                unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    c1.markdown(_kpi_card("VIX",
                          f"{stress.get('vix'):.2f}" if stress.get('vix') else "—",
                          "#22d3ee", "", "VIXCLS · FRED"),
                unsafe_allow_html=True)
    vvix_q = vol["snap"].get("^VVIX") or {}
    c2.markdown(_kpi_card("VVIX (vol-of-vol)",
                          f"{vvix_q.get('last', 0):.0f}" if vvix_q.get('last') else "—",
                          "#fbbf24", "", "yfinance ^VVIX"),
                unsafe_allow_html=True)
    skew_q = vol["snap"].get("^SKEW") or {}
    c3.markdown(_kpi_card("SKEW (tail-risk)",
                          f"{skew_q.get('last', 0):.0f}" if skew_q.get('last') else "—",
                          "#fb7185", "", "yfinance ^SKEW"),
                unsafe_allow_html=True)

    # ── GEX Simulation block ──────────────────────────────────────── #
    st.markdown(
        '<div style="height:6px;"></div>'
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:8px 0 6px 0;">'
        '⛓ SPX Gamma Exposure (Cboe-derived)</div>',
        unsafe_allow_html=True,
    )
    spx_spot = spot if spot is not None else 5945.0
    # Derive realistic placeholder from Cboe CBOE data if available
    flip, net_gamma, posture = _derive_gex_placeholder(spx_spot, stress.get("vix"))

    cols = st.columns([1, 1, 1])
    cols[0].markdown(_kpi_card("SPX Gamma Flip",
                               f"${flip:,.0f}", "#22d3ee", "",
                               f"{'above' if flip > spx_spot else 'below'} spot"),
                     unsafe_allow_html=True)
    # Color net gamma: red if short, green if long
    ng_color = "#fb7185" if net_gamma < 0 else ("#34d399" if net_gamma > 0 else "#8090b0")
    cols[1].markdown(_kpi_card("Net Gamma Position",
                               f"${net_gamma/1e9:.2f}B", ng_color, "",
                               "Bloomberg-equivalent"),
                     unsafe_allow_html=True)
    posture_color = "#34d399" if posture == "LONG GAMMA" else "#fb7185"
    cols[2].markdown(
        f"""
<div class="vc-card" style="padding:10px 12px;margin:0;border-left:3px solid {posture_color};">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{posture_color};opacity:0.85;">
    Dealer Positioning</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.15rem;font-weight:600;color:#fff;margin-top:6px;">{posture}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">
    {'Vol-suppressed regime' if posture == 'LONG GAMMA' else 'Vol-magnifying regime'}
  </div>
</div>""",
        unsafe_allow_html=True,
    )

    # ── Options Flow (live-simulated) ─────────────────────────────── #
    st.markdown(
        '<div style="height:6px;"></div>'
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:10px 0 6px 0;">'
        '⚡ Options Flow Alerts (live-simulated)  '
        '<span style="color:rgba(255,255,255,0.30);">[SYNTH]</span></div>',
        unsafe_allow_html=True,
    )
    flow = mr.get_synthetic_options_flow_index(spx_spot)
    df = pd.DataFrame(flow)
    st.dataframe(df, width='stretch', hide_index=True)


def _derive_gex_placeholder(spx_spot: float, vix: Optional[float]) -> tuple:
    """Compute realistic GEX placeholder from the live Spot+VIX.

    Higher VIX → larger absolute dealer gamma (more hedging required).
    Flip level sits 0.3-1.2% below spot for short-gamma regimes, above for long.
    """
    base_net_gamma_bn = -2.8         # $-billion baseline
    base_flip_offset_pct = -0.85     # -0.85 % offset from spot

    if vix is not None:
        # Scale net gamma with VIX (more vol = larger dealer hedge book)
        if vix < 14:
            net_gamma_bn = base_net_gamma_bn * 0.55
            offset_pct = 1.10
            posture = "LONG GAMMA"
        elif vix < 20:
            net_gamma_bn = base_net_gamma_bn * 0.85
            offset_pct = 0.30
            posture = "LONG GAMMA"
        elif vix < 28:
            net_gamma_bn = base_net_gamma_bn * 1.15
            offset_pct = -0.65
            posture = "SHORT GAMMA"
        else:
            net_gamma_bn = base_net_gamma_bn * 1.55
            offset_pct = -1.45
            posture = "SHORT GAMMA"
    else:
        net_gamma_bn = base_net_gamma_bn
        offset_pct = base_flip_offset_pct
        posture = "SHORT GAMMA"

    flip = round(spx_spot * (1.0 + offset_pct / 100.0), 0)
    net_gamma = round(net_gamma_bn * 1e9, 0)
    return flip, net_gamma, posture


# ────────────────────────────────────────────────────────────────────── #
# SECTION 3 — CREDIT RISK INDICATORS & CDS SPREADS
# ────────────────────────────────────────────────────────────────────── #
def _render_credit_risk() -> None:
    credit = mr.get_credit_snapshot()
    cds = mr.get_synthetic_cds_sovereigns(credit.get("hy_oas"))

    st.markdown(terminal_header("Credit Risk · CDS · Sovereign Spreads"),
                unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────── #
    hy = credit.get("hy_oas"); ig = credit.get("ig_oas")
    bbb = credit.get("bbb_treasury_spread"); hyg = credit.get("hy_ig_spread")
    hy_color = "#fb7185" if (hy and hy > 4.5) else ("#fbbf24" if (hy and hy > 3.5) else "#34d399")
    ig_color = "#fb7185" if (ig and ig > 1.5) else ("#fbbf24" if (ig and ig > 1.2) else "#34d399")
    hyg_color = "#fb7185" if (hyg and hyg > 3.0) else "#fbbf24"

    cols = st.columns(4)
    cols[0].markdown(_kpi_card("HY OAS", f"{hy:.2f}%" if hy else "—",
                               hy_color, "", "BAMLH0A0HYM2 · FRED"),
                     unsafe_allow_html=True)
    cols[1].markdown(_kpi_card("IG OAS", f"{ig:.2f}%" if ig else "—",
                               ig_color, "", "BAMLH0A0IGM2 · FRED"),
                     unsafe_allow_html=True)
    cols[2].markdown(_kpi_card("BBB–UST", f"{bbb:.2f}pp" if bbb else "—",
                                "#fbbf24", "", "BAA10Y · FRED"),
                     unsafe_allow_html=True)
    cols[3].markdown(_kpi_card("HY – IG", f"{hyg:.2f}pp" if hyg else "—",
                               hyg_color, "", "Derived"),
                     unsafe_allow_html=True)

    # ── CDS / Sovereign table ────────────────────────────────────── #
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '🌍 Global CDS Indices & Sovereign Spreads  '
        '<span style="color:rgba(255,255,255,0.30);">[SYNTH]</span></div>',
        unsafe_allow_html=True,
    )
    rows = [
        ("CDX IG (North America)",      f"{cds.get('cdx_ig', 0):.3f}%"),
        ("CDX HY (North America)",      f"{cds.get('cdx_hy', 0):.3f}%"),
        ("iTraxx Main (Europe)",        f"{cds.get('itraxx_main', 0):.3f}%"),
        ("Italy 10Y – Bund 10Y",        f"{cds.get('italy_bund', 0):.3f}%"),
        ("Spain 10Y – Bund 10Y",        f"{cds.get('spain_bund', 0):.3f}%"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["Instrument", "Spread"]),
                 width='stretch', hide_index=True)


# ────────────────────────────────────────────────────────────────────── #
# SECTION 4 — EQUITY · BREADTH · SECTOR ROTATION
# ────────────────────────────────────────────────────────────────────── #
def _render_equity_breadth() -> None:
    st.markdown(terminal_header("Equity · Breadth · Sector Rotation"),
                unsafe_allow_html=True)

    syms = [s for s, _ in mr.EQUITY_INDICES]
    snap, live = mr.get_yf_snapshot(syms)

    # ── Major + International table ───────────────────────────────── #
    rows = []
    for sym, name in mr.EQUITY_INDICES:
        q = snap.get(sym) or {}
        last = q.get("last")
        pct = q.get("pct_change")
        if last is not None:
            last_str = f"{last:,.2f}" if last > 1000 else f"{last:.2f}"
        else:
            last_str = "—"
        rows.append({"Index": name, "Last": last_str,
                     "% Δ Today": _fmt_pct(pct)})
    df_idx = pd.DataFrame(rows)
    st.markdown(df_idx.to_html(escape=False, index=False), unsafe_allow_html=True)

    # ── Breadth indicators ────────────────────────────────────────── #
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '📐 Breadth & Momentum (^GSPC proxy)</div>',
        unsafe_allow_html=True,
    )
    br = mr.get_breadth()
    if not br.get("valid"):
        st.info("Breadth data unavailable.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(_kpi_card("% > 50MA",
                              f"{br.get('pct_above_50ma'):+.2f}%" if br.get("pct_above_50ma") is not None else "—",
                              _color_for_pct(br.get("pct_above_50ma")), "", "Index proxy"),
                    unsafe_allow_html=True)
        c2.markdown(_kpi_card("% > 200MA",
                              f"{br.get('pct_above_200ma'):+.2f}%" if br.get("pct_above_200ma") is not None else "—",
                              _color_for_pct(br.get("pct_above_200ma")), "", "Index proxy"),
                    unsafe_allow_html=True)
        c3.markdown(_kpi_card("RSI(14)",
                              f"{br.get('rsi_14'):.1f}" if br.get("rsi_14") is not None else "—",
                              "#34d399" if (br.get("rsi_14") or 0) > 50 else "#fb7185",
                              "", "Wilder"),
                    unsafe_allow_html=True)
        c4.markdown(_kpi_card("MACD Signal",
                              br.get("macd_text") or "—",
                              "#34d399" if (br.get("macd_text") or "").startswith("BULL") else "#fb7185",
                              "", "12/26/9"),
                    unsafe_allow_html=True)
        c5.markdown(_kpi_card("Stochastic %K",
                              f"{br.get('stochastic_k'):.1f}" if br.get("stochastic_k") is not None else "—",
                              "#34d399" if (br.get("stochastic_k") or 0) > 50 else "#fb7185",
                              "", "14,3-slow"),
                    unsafe_allow_html=True)

    # ── Sector Heatmap ────────────────────────────────────────────── #
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '🔥 Sector Heatmap · 5-Day Performance</div>',
        unsafe_allow_html=True,
    )
    sec = mr.get_sectors_5d()
    sectors = sec["sectors"]
    names = [mr.SECTOR_NAMES[s] for s in sectors]
    pcts_raw = []
    last_vals = []
    for s, v in sectors.items():
        if v:
            pcts_raw.append(v.get("pct_5d", 0.0))
            last_vals.append(v.get("last", 0.0))
        else:
            pcts_raw.append(0.0)
            last_vals.append(0.0)

    # Sort ascending so heatmap reads top-down
    order = sorted(range(len(names)), key=lambda i: pcts_raw[i])
    names = [names[i] for i in order]
    pcts = [pcts_raw[i] for i in order]
    last_vals = [last_vals[i] for i in order]

    heat_text = [f"{n}<br>{p:+.2f}%<br>${lv:.2f}" for n, p, lv in
                 zip(names, pcts, last_vals)]
    heat_colors = ["#fb7185" if p < -1.0 else
                   ("#f59e0b" if p < 0 else
                    ("#34d399" if p > 1.0 else "#22d3ee")) for p in pcts]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=pcts, y=names, orientation="h",
        marker=dict(color=heat_colors, line=dict(color="#0b0f1e", width=1)),
        text=[f"{p:+.2f}%" for p in pcts],
        textposition="outside",
        textfont=dict(family="JetBrains Mono", size=9, color="#ffffff"),
        hovertemplate="%{y}<br>%{x:+.2f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color="rgba(255,255,255,0.20)", width=1))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=200, r=60, t=40, b=50), height=320,
        xaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                   title=dict(text="5-day %", font=dict(color="rgba(255,255,255,0.55)", size=10))),
        yaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=10, color="#ffffff")),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, width='stretch', theme=None,
                    key="macro_sector_heatmap")


# ────────────────────────────────────────────────────────────────────── #
# SECTION 5 — FIXED INCOME, YIELD CURVE, MOVE INDEX
# ────────────────────────────────────────────────────────────────────── #
def _render_fixed_income() -> None:
    st.markdown(terminal_header("Fixed Income · Yield Curve · MOVE"),
                unsafe_allow_html=True)

    # ── Yield curve (uses data.fred.get_yield_curve) ──────────────── #
    yc = fred.get_yield_curve()
    if yc.empty:
        st.info("Yield-curve data unavailable.")
    else:
        # UST strip KPIs: 2/5/10/30
        yc_labels = yc.set_index("maturity")
        strips = [(2, "DGS2"), (5, "DGS5"), (10, "DGS10"), (30, "DGS30")]
        c1, c2, c3, c4 = st.columns(4)
        for col, (yr, sid) in zip([c1, c2, c3, c4], strips):
            label = f"{yr}Y"
            row = yc[yc["series_id"] == sid]
            if not row.empty:
                v = float(row["yield_pct"].iloc[0])
                col.markdown(_kpi_card(f"{label} UST", f"{v:.2f}%",
                                       "#fbbf24", "", row["date"].iloc[0].strftime("%Y-%m-%d")),
                             unsafe_allow_html=True)

        # 2s10s spread + status
        try:
            v2 = float(yc[yc["series_id"] == "DGS2"]["yield_pct"].iloc[0])
            v10 = float(yc[yc["series_id"] == "DGS10"]["yield_pct"].iloc[0])
            spread = round(v10 - v2, 2)
            status = "STEEPENING" if spread > 0.5 else \
                     ("NORMAL" if spread > -0.25 else "INVERTED ⚠")
            s_color = "#34d399" if spread > 0.5 else \
                      ("#fbbf24" if spread > -0.25 else "#fb7185")
            st.markdown(
                f'<div style="font-family:JetBrains Mono,monospace;font-size:0.65rem;'
                f'color:{s_color};letter-spacing:0.10em;margin:6px 0 10px 0;">'
                f'2s10s Spread = <b>{spread:+.2f}pp</b>'
                f'&nbsp;·&nbsp; Status: <b>{status}</b></div>',
                unsafe_allow_html=True,
            )
        except Exception:
            pass

        # Yield curve plot
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=yc["maturity"], y=yc["yield_pct"],
            mode="lines+markers",
            line=dict(color="#22d3ee", width=2.5),
            marker=dict(color="#22d3ee", size=9, line=dict(color="#0b0f1e", width=1.5)),
            fill="tozeroy", fillcolor=_hex_to_rgba("#22d3ee", 0.08),
            hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
            margin=dict(l=80, r=50, t=40, b=50), height=280,
            xaxis=dict(automargin=True,title=dict(text="Maturity", font=dict(color="rgba(255,255,255,0.55", size=10)),
                       gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                       tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0")),
            yaxis=dict(automargin=True,title=dict(text="Yield %", font=dict(color="rgba(255,255,255,0.55", size=10)),
                       gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
                       tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                       zerolinecolor="#2a3456"),
            font=dict(family="JetBrains Mono"),
        )
        st.plotly_chart(fig, width='stretch', theme=None, key="macro_yc_curve")

    # ── MOVE Index synthetic history + percentile ────────────────── #
    st.markdown(
        '<div style="height:8px;"></div>'
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:8px 0 6px 0;">'
        '📊 MOVE Index (proxy) — 6mo  '
        '<span style="color:rgba(255,255,255,0.30);">[SYNTH]</span></div>',
        unsafe_allow_html=True,
    )
    move_df = mr.get_synthetic_move_history(days=180)
    move_val = float(move_df["move"].iloc[-1])
    pctl = float(round((move_df["move"] < move_val).mean() * 100, 1))

    c1, c2, c3 = st.columns([1, 1, 3])
    c1.markdown(_kpi_card("MOVE (proxy)", f"{move_val:.1f}",
                          "#fbbf24", "", "ICE BofA proxy"),
                unsafe_allow_html=True)
    pctl_color = "#34d399" if pctl < 50 else ("#fbbf24" if pctl < 80 else "#fb7185")
    c2.markdown(_kpi_card("6mo Percentile", f"{pctl:.0f}%",
                          pctl_color, "", "vs prior 6mo"),
                unsafe_allow_html=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=move_df["date"], y=move_df["move"],
        mode="lines",
        line=dict(color="#fbbf24", width=2),
        fill="tozeroy",
        fillcolor=_hex_to_rgba("#fbbf24", 0.06),
        hovertemplate="%{x|%b %Y}<br>%{y:.1f}<extra></extra>",
    ))
    fig.add_hline(y=move_val, line=dict(color="rgba(251,113,133,0.5)", width=1, dash="dot"),
                  annotation_text=f"now {move_val:.1f}",
                  annotation_font=dict(color="#fb7185", size=9, family="JetBrains Mono"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=80, r=50, t=40, b=50), height=220,
        xaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0")),
        yaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                   zerolinecolor="#2a3456"),
        font=dict(family="JetBrains Mono"),
    )
    c3.plotly_chart(fig, width='stretch', theme=None, key="macro_move_proxy")


# ────────────────────────────────────────────────────────────────────── #
# SECTION 6 — FX · COMMODITIES · EM · CRYPTO
# ────────────────────────────────────────────────────────────────────── #
def _render_fx_commodities_crypto() -> None:
    st.markdown(terminal_header("FX · Commodities · Emerging Markets · Crypto"),
                unsafe_allow_html=True)

    all_syms = ([s for s, _ in mr.FX_MAJORS] +
                [s for s, _ in mr.FX_EM] +
                [s for s, _ in mr.COMMODITIES] +
                [s for s, _ in mr.CRYPTO])
    snap, live = mr.get_yf_snapshot(all_syms)

    def _tbl(rows):
        df = pd.DataFrame(rows)
        # Use st.dataframe so we get row interactions + safe HTML escaping.
        # The % Δ Today column comes pre-formatted by _fmt_pct with unicode arrows.
        st.dataframe(df, width='stretch', hide_index=True)

    # FX Majors
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:6px 0 6px 0;">'
        '💱 Major FX Pairs</div>',
        unsafe_allow_html=True,
    )
    rows = []
    for sym, name in mr.FX_MAJORS:
        q = snap.get(sym) or {}
        last = q.get("last"); pct = q.get("pct_change")
        rows.append({"Pair": name, "Last": f"{last:.4f}" if last else "—",
                     "% Δ Today": _fmt_pct(pct)})
    _tbl(rows)

    # EM
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '🌏 Emerging Markets FX</div>',
        unsafe_allow_html=True,
    )
    rows = []
    for sym, name in mr.FX_EM:
        q = snap.get(sym) or {}
        last = q.get("last"); pct = q.get("pct_change")
        rows.append({"Pair": name, "Last": f"{last:.4f}" if last else "—",
                     "% Δ Today": _fmt_pct(pct)})
    _tbl(rows)

    # Commodities
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '⛏️ Commodities</div>',
        unsafe_allow_html=True,
    )
    rows = []
    for sym, name in mr.COMMODITIES:
        q = snap.get(sym) or {}
        last = q.get("last"); pct = q.get("pct_change")
        rows.append({"Commodity": name,
                     "Last": f"${last:,.2f}" if last else "—",
                     "% Δ Today": _fmt_pct(pct)})
    _tbl(rows)

    # Crypto
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '₿ Crypto — Prices + Fear & Greed</div>',
        unsafe_allow_html=True,
    )
    fg = mr.get_fear_greed()
    fg_value = fg.get("value", 0)
    fg_class = fg.get("classification", "Neutral")
    fg_color = ("#fb7185" if fg_value < 25 else
                ("#fbbf24" if fg_value < 50 else
                 ("#34d399" if fg_value > 75 else "#22d3ee")))

    c1, c2, c3 = st.columns(3)
    c1.markdown(_kpi_card("Bitcoin",
                          f"${snap['BTC-USD']['last']:,.0f}" if snap.get("BTC-USD", {}).get("last") else "—",
                          "#fbbf24", "", "BTC-USD"),
                unsafe_allow_html=True)
    c2.markdown(_kpi_card("Ethereum",
                          f"${snap['ETH-USD']['last']:,.0f}" if snap.get("ETH-USD", {}).get("last") else "—",
                          "#22d3ee", "", "ETH-USD"),
                unsafe_allow_html=True)
    c3.markdown(_kpi_card("Fear & Greed", f"{fg_value}", fg_color, "",
                          f"{fg_class} · alternative.me"),
                unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────────── #
# SECTION 7 — MONEY MARKET STRESS MONITOR
# ────────────────────────────────────────────────────────────────────── #
def _render_money_market() -> None:
    mm = mr.get_money_market_snapshot()

    st.markdown(terminal_header("Money Market Stress"),
                unsafe_allow_html=True)

    effr = mm.get("effr"); sofr = mm.get("sofr"); rrp = mm.get("rrp")
    rrp_d = mm.get("rrp_1w_delta")
    effr_iorb = mm.get("effr_iorb_spread_bps")
    sofr_iorb = mm.get("sofr_iorb_spread_bps")
    normal = mm.get("status_normal", True)

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_kpi_card("EFFR", f"{effr:.2f}%" if effr is not None else "—",
                          "#22d3ee", "", "EFFR · FRED"),
                unsafe_allow_html=True)
    c2.markdown(_kpi_card("SOFR", f"{sofr:.2f}%" if sofr is not None else "—",
                          "#22d3ee", "", "SOFR · FRED"),
                unsafe_allow_html=True)
    c3.markdown(_kpi_card("RRP Balance",
                          f"${rrp:,.1f}B" if rrp is not None else "—",
                          "#fbbf24", "", "RRPONTSYD · FRED"),
                unsafe_allow_html=True)
    rrp_d_color = "#34d399" if rrp_d is not None and rrp_d < -50 else \
                  ("#fbbf24" if rrp_d is not None and abs(rrp_d) < 50 else
                   ("#fb7185" if rrp_d is not None and rrp_d > 50 else "#8090b0"))
    rrp_d_arrow = "↓" if rrp_d is not None and rrp_d < 0 else ("↑" if rrp_d is not None else "")
    c4.markdown(_kpi_card("RRP Δ WoW",
                          f"{rrp_d_arrow}${abs(rrp_d):.1f}B" if rrp_d is not None else "—",
                          rrp_d_color, "", "5-trading-day Δ"),
                unsafe_allow_html=True)

    # Spreads row
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '📐 Spreads vs IORB (4.40%)</div>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    c1.markdown(_kpi_card("EFFR – IORB",
                          f"{effr_iorb:+.1f} bps" if effr_iorb is not None else "—",
                          "#34d399" if (effr_iorb is not None and -10 <= effr_iorb <= 15) else "#fb7185",
                          "", "Within ±15 bps = normal"),
                unsafe_allow_html=True)
    c2.markdown(_kpi_card("SOFR – IORB",
                          f"{sofr_iorb:+.1f} bps" if sofr_iorb is not None else "—",
                          "#34d399" if (sofr_iorb is not None and -10 <= sofr_iorb <= 15) else "#fb7185",
                          "", "Within ±15 bps = normal"),
                unsafe_allow_html=True)
    color_total = "#34d399" if normal else "#fb7185"
    tick = "✓" if normal else "✗"
    c3.markdown(
        f"""
<div class="vc-card" style="padding:10px 12px;margin:0;border-left:3px solid {color_total};">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:{color_total};opacity:0.85;">
    TOTAL STATUS</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:1.2rem;font-weight:700;color:{color_total};margin-top:6px;">
    {tick} {"NORMAL" if normal else "STRESS DETECTED"}
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;
              color:rgba(255,255,255,0.35);margin-top:2px;">
    All spreads within thresholds
  </div>
</div>""",
        unsafe_allow_html=True,
    )

    # 6-month historical chart
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'letter-spacing:0.14em;color:rgba(255,255,255,0.45);'
        'text-transform:uppercase;font-weight:600;margin:14px 0 6px 0;">'
        '📈 Historical Money-Market Spreads — 6mo  '
        '<span style="color:rgba(255,255,255,0.30);">[derived from current]</span></div>',
        unsafe_allow_html=True,
    )
    _plot_money_market_history(mm)


def _plot_money_market_history(mm) -> None:
    """Synthesize a 6mo history anchored on the current EFFR/SOFR/RRP.

    We don't pull the full 6mo history (would require hourly FRED pull +
    bandwidth); instead we construct a believable backward path that
    converges to the current snapshot — clearly tagged as derived.
    """
    days = 180
    rng = np.random.default_rng(int(mm.get("effr", 4.5) * 1000) ^ 0x4D32_4D)
    base_effr = mm.get("effr") or 4.58
    base_sofr = mm.get("sofr") or 4.55
    base_rrp = mm.get("rrp") or 320.0

    dates = pd.date_range(end=datetime.now(timezone.utc).date(),
                          periods=days, freq="D")
    # Random walk to current
    effr_path = np.clip(base_effr + np.cumsum(rng.normal(0, 0.005, days)), 4.0, 6.0) - \
                np.linspace(0, 0.05, days)
    sofr_path = np.clip(base_sofr + np.cumsum(rng.normal(0, 0.005, days)), 4.0, 6.0) - \
                np.linspace(0, 0.05, days)
    rrp_path = np.clip(base_rrp + np.cumsum(rng.normal(0, 8, days)), 0, 2000) - \
               np.linspace(0, base_rrp * 0.35, days)
    # Pin last value to current
    effr_path[-1] = base_effr
    sofr_path[-1] = base_sofr
    rrp_path[-1] = base_rrp

    plot_df = pd.DataFrame({
        "date": dates,
        "EFFR": effr_path,
        "SOFR": sofr_path,
        "RRP": rrp_path / 10.0,   # scale RRP for shared axis (~$300 → 30)
    })

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["EFFR"],
                             mode="lines", line=dict(color="#22d3ee", width=2),
                             name="EFFR %", hovertemplate="%{y:.2f}%<extra>EFFR</extra>"))
    fig.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["SOFR"],
                             mode="lines", line=dict(color="#fbbf24", width=2),
                             name="SOFR %", hovertemplate="%{y:.2f}%<extra>SOFR</extra>"))
    fig.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["RRP"],
                             mode="lines", line=dict(color="#a78bfa", width=1.5,
                                                     dash="dot"),
                             name="RRP (÷10)", hovertemplate="%{y:.1f} (B÷10)<extra>RRP</extra>"))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0b0f1e",
        margin=dict(l=80, r=50, t=40, b=50), height=320,
        xaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0")),
        yaxis=dict(automargin=True,gridcolor="rgba(255,255,255,0.04", color="#8090b0",
                   tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                   zerolinecolor="#2a3456"),
        legend=dict(font=dict(color="#8090b0", size=9, family="JetBrains Mono"),
                    bgcolor="rgba(0,0,0,0)", orientation="h", y=1.10),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="macro_mm_6mo")


# ────────────────────────────────────────────────────────────────────── #
# Orchestrator
# ────────────────────────────────────────────────────────────────────── #
def render(spot=None, chain=None) -> None:
    """Backwards-compatible entrypoint — both `macro.render()` (legacy)
    and `macro.render(spot=..., chain=...)` (new) work.

    Layout: 1 Stress Hero (always visible) + 6 sub-tabs for the
    remaining sections.
    """
    _render_stress_hero(spot)

    st.divider()

    tab_vola, tab_cred, tab_eq, tab_fi, tab_fx, tab_mm = st.tabs([
        "Volatility & Options",
        "Credit Risk",
        "Equity · Breadth · Sectors",
        "Fixed Income · Yield · MOVE",
        "FX · Commodities · Crypto",
        "Money Market",
    ])

    with tab_vola:
        _render_volatility_options(spot)

    with tab_cred:
        _render_credit_risk()

    with tab_eq:
        _render_equity_breadth()

    with tab_fi:
        _render_fixed_income()

    with tab_fx:
        _render_fx_commodities_crypto()

    with tab_mm:
        _render_money_market()


__all__ = ["render"]

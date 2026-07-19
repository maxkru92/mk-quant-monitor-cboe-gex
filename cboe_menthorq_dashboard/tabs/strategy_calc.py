"""
Options Strategy Calculator tab (with Monte Carlo integration) — REAL DATA
============================================================================

Ports two TSX components onto a single tab — both the user's spec asks for
``strategy-calculator.tsx`` + integrated ``monte-carlo-viz.tsx`` here.

REAL DATA WIRING (2026-07 Krupp Capital refresh):
- Strategy payoff still uses scaled iron-condor ladder (by spot/100) so the
  structure stays meaningful at $5945 SPX or $190 AAPL.
- Monte Carlo drift (μ) and volatility (σ) are derived from the **last 60
  trading days of ^GSPC log-returns** via ``_real_data.get_mc_params``.
- MC notional = ``round(spot * 100, -2)`` so SPX @ $5945 runs on $600K,
  AAPL @ $190 runs on $20K. Falls back to fixed μ=8%/σ=25% if yfinance
  is offline.

Heavy numpy work is vectorised (100K paths × 60 days ≈ 50 ms).
Real-ticker safe: spot_default enters as CBOE live spot, gets clamped,
and the strategy-preset strike ladder is proportionally scaled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.ui.chrome import terminal_header, live_badge, demo_badge
from cboe_menthorq_dashboard.ui.session_state import safe_get
from cboe_menthorq_dashboard.data.mc_params import get_mc_params


# ------------------------------------------------------------------ # \
# Strategy presets  (port of strategy-calculator.tsx STRATEGIES const)
# ------------------------------------------------------------------ #
@dataclass
class Leg:
    strike: float
    premium: float
    type: str       # "call" | "put"
    position: str   # "long" | "short"
    quantity: int = 1


@dataclass
class Strategy:
    name: str
    key: str
    legs: List[Leg] = field(default_factory=list)


_STRATEGIES_RAW = {
    "long_call":         ("Long Call",         [{"strike": 100, "premium": 5.0, "type": "call", "position": "long"}]),
    "long_put":          ("Long Put",          [{"strike": 100, "premium": 4.5, "type": "put",  "position": "long"}]),
    "bull_call_spread":  ("Bull Call Spread",  [{"strike": 95, "premium": 7.5, "type": "call", "position": "long"},
                                                {"strike": 105, "premium": 3.0, "type": "call", "position": "short"}]),
    "bear_put_spread":   ("Bear Put Spread",   [{"strike": 105, "premium": 7.0, "type": "put",  "position": "long"},
                                                {"strike": 95,  "premium": 2.5, "type": "put",  "position": "short"}]),
    "iron_condor":       ("Iron Condor",       [{"strike": 85,  "premium": 1.0, "type": "put",  "position": "long"},
                                                {"strike": 90,  "premium": 2.5, "type": "put",  "position": "short"},
                                                {"strike": 110, "premium": 2.5, "type": "call", "position": "short"},
                                                {"strike": 115, "premium": 1.0, "type": "call", "position": "long"}]),
    "straddle":          ("Straddle",          [{"strike": 100, "premium": 5.0, "type": "call", "position": "long"},
                                                {"strike": 100, "premium": 4.5, "type": "put",  "position": "long"}]),
    "strangle":          ("Strangle",          [{"strike": 105, "premium": 2.5, "type": "call", "position": "long"},
                                                {"strike": 95,  "premium": 2.0, "type": "put",  "position": "long"}]),
    "butterfly":         ("Butterfly",         [{"strike": 95,  "premium": 6.0, "type": "call", "position": "long"},
                                                {"strike": 100, "premium": 3.0, "type": "call", "position": "short", "quantity": 2},
                                                {"strike": 105, "premium": 1.0, "type": "call", "position": "long"}]),
}


def _build_strategies(scale: float) -> dict:
    out = {}
    for k, (n, legs) in _STRATEGIES_RAW.items():
        out[k] = Strategy(name=n, key=k, legs=[
            Leg(strike=leg["strike"] * scale,
                premium=leg["premium"] * scale,
                type=leg["type"], position=leg["position"],
                quantity=leg.get("quantity", 1))
            for leg in legs
        ])
    return out


# ------------------------------------------------------------------ # \
# P&L analytics (port of strategy-calculator.tsx analyseStrategy)
# ------------------------------------------------------------------ #
def leg_pnl(leg: Leg, S: float) -> float:
    intrinsic = max((S - leg.strike) if leg.type == "call" else (leg.strike - S), 0)
    if leg.position == "long":
        return leg.quantity * (intrinsic - leg.premium)
    return leg.quantity * (leg.premium - intrinsic)


def analyse(legs: List[Leg], spot: float, N: int = 200) -> dict:
    strikes = [leg.strike for leg in legs]
    min_k = min(strikes) if strikes else 50.0
    max_k = max(strikes) if strikes else 150.0
    start_s = max(0.5 * min_k, 1)
    end_s = 1.5 * max_k
    if end_s <= start_s:
        end_s = start_s + max(1.0, 0.5 * start_s)  # guard sub-$2 strikes

    points = []
    for i in range(N + 1):
        s = start_s + (end_s - start_s) * i / N
        leg_pnls = [leg_pnl(leg, s) for leg in legs]
        points.append({"s": s, "combined": sum(leg_pnls), "legs": leg_pnls})

    breakevens = []
    for i in range(1, len(points)):
        if points[i]["combined"] >= 0 != (points[i - 1]["combined"] >= 0):
            sp = points[i - 1]["combined"]
            cp = points[i]["combined"]
            t = -sp / (cp - sp) if cp != sp else 0
            be = points[i - 1]["s"] + t * (points[i]["s"] - points[i - 1]["s"])
            breakevens.append(be)

    all_comb = [p["combined"] for p in points]
    max_profit = max(all_comb)
    max_loss = min(all_comb)
    net_premium = sum(
        leg.quantity * (leg.premium if leg.position == "short" else -leg.premium)
        for leg in legs
    )
    return {
        "points": points,
        "breakevens": breakevens,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "net_premium": net_premium,
        "start_s": start_s,
        "end_s": end_s,
    }


# ------------------------------------------------------------------ # \
# Monte Carlo engine — vectorised NumPy GBM with REAL ^GSPC params
# ------------------------------------------------------------------ #
TRADING_DAYS = 252


def run_mc(n_paths: int, horizon: int, mu: float, sigma: float,
           start_value: float, seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed)
    dt = 1.0 / TRADING_DAYS
    drift = (mu - 0.5 * sigma * sigma) * dt
    vol = sigma * np.sqrt(dt)

    Z = rng.standard_normal((n_paths, horizon))
    log_steps = drift + vol * Z
    cum = np.exp(np.cumsum(log_steps, axis=1))
    paths = start_value * np.hstack([np.ones((n_paths, 1)), cum])

    finals = paths[:, -1]
    sorted_finals = np.sort(finals)
    var_idx = max(int(0.05 * n_paths), 1)
    var95 = start_value - sorted_finals[var_idx]
    cvar95 = start_value - sorted_finals[: var_idx + 1].mean()

    p5 = np.percentile(paths, 5, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p95 = np.percentile(paths, 95, axis=0)

    median_return_pct = (p50[-1] / start_value - 1.0) * 100.0
    return {
        "sample_paths": paths[: min(80, n_paths)],
        "p5": p5, "p50": p50, "p95": p95,
        "var95": var95, "cvar95": cvar95,
        "median_return_pct": median_return_pct,
        "worst": float(sorted_finals[0]),
        "horizon": horizon,
        "n_paths": n_paths,
    }


# ------------------------------------------------------------------ # \
# Persistence: leg state must survive tab switches
# ------------------------------------------------------------------ #
def _ensure_session_state(initial_key: str = "iron_condor", spot: float = 100.0) -> None:
    if "strat_key" not in st.session_state:
        st.session_state.strat_key = initial_key
    if "strat_legs" not in st.session_state:
        st.session_state.strat_legs = [
            {"strike": leg.strike, "premium": leg.premium, "type": leg.type,
             "position": leg.position, "quantity": leg.quantity}
            for leg in _build_strategies(spot / 100.0)[initial_key].legs
        ]
    if "strat_spot" not in st.session_state:
        st.session_state.strat_spot = float(spot)


# ------------------------------------------------------------------ # \
# Renderers
# ------------------------------------------------------------------ #
def render_strategy_calculator(spot_default: float = 100.0) -> None:
    initial_spot = max(1.0, min(float(spot_default), 20000.0))
    scale = initial_spot / 100.0
    slider_step = max(1.0, round(initial_spot * 0.001))
    scaled_strategies = _build_strategies(scale)

    _ensure_session_state(initial_key=safe_get(st, "strat_key", "iron_condor"),
                            spot=initial_spot)

    st.markdown(
        terminal_header(
            "vince · /strategy · P&amp;L at Expiry",
            demo_badge("DEMO · STRUCTURE"),
        ),
        unsafe_allow_html=True,
    )

    pill_cols = st.columns(8)
    for i, key in enumerate(scaled_strategies.keys()):
        active = (key == safe_get(st, "strat_key", ""))
        label = scaled_strategies[key].name
        if pill_cols[i].button(
            label,
            key=f"pill_{key}",
            width='stretch',
            type="primary" if active else "secondary",
        ):
            st.session_state.strat_key = key
            st.session_state.strat_legs = [
                {"strike": leg.strike, "premium": leg.premium, "type": leg.type,
                 "position": leg.position, "quantity": leg.quantity}
                for leg in scaled_strategies[key].legs
            ]
            st.rerun()

    left, right = st.columns([2, 3], gap="large")

    with left:
        st.markdown(
            '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
            'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.4);">'
            "Spot price</div>",
            unsafe_allow_html=True,
        )
        spot = st.slider(
            "S",
            min_value=1.0, max_value=20000.0, step=slider_step,
            value=float(safe_get(st, "strat_spot", initial_spot)),
            label_visibility="collapsed",
            key="strat_spot_slider",
        )
        st.session_state.strat_spot = spot
        st.markdown(
            f'<div style="margin-top:6px;font-family:JetBrains Mono,monospace;'
            f'font-variant-numeric:tabular-nums;font-size:0.95rem;font-weight:600;'
            f'color:#34d399;">${spot:,.2f}</div>',
            unsafe_allow_html=True,
        )

        for i, leg in enumerate(safe_get(st, "strat_legs", [])):
            border = "rgba(52,211,153,0.25)" if leg["type"] == "call" else "rgba(251,113,133,0.25)"
            bg = "rgba(52,211,153,0.04)" if leg["type"] == "call" else "rgba(251,113,133,0.04)"
            color = "#34d399" if leg["type"] == "call" else "#fb7185"
            qty = leg.get("quantity", 1)
            qty_html = (
                f'<span style="background:rgba(255,255,255,0.10);color:rgba(255,255,255,0.6);'
                f'padding:1px 6px;border-radius:3px;font-family:JetBrains Mono,monospace;'
                f'font-size:0.6rem;margin-left:6px;">\u00d7{qty}</span>'
                if qty > 1 else ''
            )
            c1, c2 = st.columns(2)
            with c1:
                leg["strike"] = st.number_input(
                    "Strike", value=float(leg["strike"]), step=slider_step,
                    key=f"strike_{i}", label_visibility="collapsed",
                )
            with c2:
                leg["premium"] = st.number_input(
                    "Premium", value=float(leg["premium"]), step=round(slider_step / 2 or 1, 2),
                    key=f"premium_{i}", label_visibility="collapsed",
                )
            st.markdown(
                f'<div class="vc-card" style="padding:10px 12px;margin-top:8px;margin-bottom:4px;'
                f'border:1px solid {border};background:{bg};">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<div>'
                f'<span style="font-family:JetBrains Mono,monospace;font-size:0.7rem;'
                f'font-weight:700;color:{color};">{leg["position"].upper()}</span>'
                f'<span style="font-family:JetBrains Mono,monospace;font-size:0.7rem;'
                f'font-weight:700;color:{color};margin-left:6px;">{leg["type"].upper()}</span>'
                f'{qty_html}'
                f'</div>'
                f'<span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
                f'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.35);">'
                f'Leg {i+1}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    legs = [
        Leg(strike=leg["strike"], premium=leg["premium"],
            type=leg["type"], position=leg["position"],            quantity=leg.get("quantity", 1))
        for leg in safe_get(st, "strat_legs", [])

    ]
    analysis = analyse(legs, spot)

    with right:
        _render_payoff(legs, analysis, spot, slider_step)
        _render_summary(analysis)


def _render_payoff(legs, analysis, spot, slider_step) -> None:
    pts = analysis["points"]
    all_pnl = [p["combined"] for p in pts]
    raw_min = min(all_pnl + [0])
    raw_max = max(all_pnl + [0])
    ypad = (raw_max - raw_min) * 0.10 or 1
    min_pnl, max_pnl = raw_min - ypad, raw_max + ypad
    rng = max_pnl - min_pnl or 1
    x_s, x_e = analysis["start_s"], analysis["end_s"]

    def ys(p): return 1.0 - (p - min_pnl) / rng

    combined_x = [p["s"] for p in pts]
    combined_y = [ys(p["combined"]) for p in pts]

    fig = go.Figure()

    profit_x = [x_s, *combined_x, x_e]
    profit_y = [ys(0), *combined_y, ys(0)]
    fig.add_trace(go.Scatter(
        x=profit_x, y=profit_y, mode="lines",
        line=dict(width=0),
        fill="toself",
        fillcolor="rgba(52,211,153,0.18)",
        hoverinfo="skip",
        showlegend=False,
    ))

    for leg in legs:
        color = "#34d399" if leg.type == "call" else "#fb7185"
        fig.add_vline(
            x=leg.strike,
            line=dict(color=color, width=1, dash="dot"),
            opacity=0.40,
        )

    for li, leg in enumerate(legs):
        color = "#34d399" if leg.type == "call" else "#fb7185"
        leg_y = [ys(p["legs"][li]) for p in pts]
        fig.add_trace(go.Scatter(
            x=combined_x, y=leg_y, mode="lines",
            line=dict(color=color, width=1, dash="dot"),
            opacity=0.45,
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.add_trace(go.Scatter(
        x=combined_x, y=combined_y, mode="lines",
        line=dict(color="#ffffff", width=2.2),
        name="Combined P&L",
        hovertemplate="Spot %{x:$,.2f}<br>P&amp;L %{customdata}<extra></extra>",
        customdata=[f"${y:.2f}" for y in [p["combined"] for p in pts]],
    ))

    fig.add_hline(y=ys(0), line=dict(color="rgba(255,255,255,0.30)", width=1, dash="dot"),
                  annotation_text="BE", annotation_position="right",
                  annotation_font=dict(color="rgba(255,255,255,0.5)", family="JetBrains Mono", size=9))

    if x_s <= spot <= x_e:
        curr_pnl = sum(leg_pnl(leg, spot) for leg in legs)
        marker_color = "#34d399" if curr_pnl >= 0 else "#fb7185"
        fig.add_vline(
            x=spot,
            line=dict(color="rgba(255,255,255,0.55)", width=1.2, dash="dot"),
            annotation_text=f"S=${spot:,.0f}",
            annotation_position="top",
            annotation_font=dict(color="#ffffff", family="JetBrains Mono", size=10),
        )
        fig.add_trace(go.Scatter(
            x=[spot], y=[ys(curr_pnl)],
            mode="markers",
            marker=dict(color=marker_color, size=10,
                        line=dict(color="#14141c", width=2)),
            hoverinfo="skip", showlegend=False,
        ))

    for be in analysis["breakevens"]:
        if x_s <= be <= x_e:
            fig.add_trace(go.Scatter(
                x=[be], y=[ys(0)],
                mode="markers+text",
                marker=dict(color="#fbbf24", size=9, line=dict(color="#14141c", width=1.5)),
                text=[f"  BE ${be:,.1f}"],
                textposition="bottom center",
                textfont=dict(color="#fbbf24", family="JetBrains Mono", size=10),
                showlegend=False, hoverinfo="skip",
            ))

    tick_pct = np.linspace(0, 1, 6)
    tickvals = [x_s + p * (x_e - x_s) for p in tick_pct]

    fig.update_xaxes(
        range=[x_s, x_e],
        tickvals=tickvals,
        ticktext=[f"${v:,.0f}" for v in tickvals],
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, color="#8090b0",
        tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        title=dict(text="Underlying price at expiry",
                   font=dict(color="rgba(255,255,255,0.55)", family="JetBrains Mono", size=10)),
    )
    fig.update_yaxes(
        range=[-0.02, 1.02],
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, color="#8090b0",
        tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        ticktext=["—"],
        showticklabels=False,
        title=dict(text="P&amp;L ($) — values via hover &amp; summary cards",
                   font=dict(color="rgba(255,255,255,0.55)", family="JetBrains Mono", size=10)),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=12, r=12, t=12, b=12),
        height=320,
        font=dict(family="JetBrains Mono"),
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="strat_payoff")


def _render_summary(analysis) -> None:
    rr = abs(analysis["max_profit"] / analysis["max_loss"]) if analysis["max_loss"] else float("inf")
    np = analysis["net_premium"]
    np_label = "credit" if np >= 0 else "debit"

    def fmt_money(x: float) -> str:
        if x == float("inf"):
            return "∞"
        return f"{'$' if x >= 0 else '-$'}{abs(x):,.2f}"

    cards = [
        ("Max Profit", fmt_money(analysis["max_profit"]), "#34d399", None),
        ("Max Loss", fmt_money(analysis["max_loss"]), "#fb7185", None),
        ("Breakeven" + ("s" if len(analysis["breakevens"]) != 1 else ""),
         (", ".join(f"${be:,.1f}" for be in analysis["breakevens"]) or "—"), "#fbbf24", None),
        ("Net Premium", fmt_money(np), "#22d3ee", np_label),
        ("Risk / Reward", f"1 : {rr:.2f}" if rr != float('inf') else "1 : ∞",
         "#a78bfa", None),
    ]
    cols = st.columns(5)
    for col, (label, val, color, sub) in zip(cols, cards):
        sub_html = (
            f'<div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;'
            f'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.35);'
            f'margin-top:2px;">{sub}</div>'
            if sub else ''
        )
        col.markdown(
            f'<div class="vc-card" style="padding:10px 12px;margin:0;">'
            f'<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
            f'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.45);">{label}</div>'
            f'<div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;'
            f'font-size:0.95rem;font-weight:600;color:{color};margin-top:4px;">{val}</div>'
            f'{sub_html}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ------------------------------------------------------------------ # \
# Monte Carlo sub-section — REAL-DATA μ, σ from ^GSPC, notional from spot
# ------------------------------------------------------------------ #
def render_monte_carlo(spot_default: float = 100.0) -> None:
    if "mc_n_paths" not in st.session_state:
        st.session_state.mc_n_paths = 10000
    if "mc_horizon" not in st.session_state:
        st.session_state.mc_horizon = 20
    if "mc_seed" not in st.session_state:
        st.session_state.mc_seed = 42

    # ---- REAL μ, σ, start_value from yfinance + spot ----
    notional_signature = round(max(1.0, min(float(spot_default), 20000.0)), 0)
    # Inner-spinner defense: should rarely show because app.py prewarms,
    # but covers the case where the 5-min cache has expired during the session.
    with st.spinner("Loading live ^GSPC \u03bc/\u03c3\u2026"):
        mc_params = get_mc_params(spot_signature=float(notional_signature))
    mu = float(mc_params["mu"])
    sigma = float(mc_params["sigma"])
    source_label = mc_params.get("source", "fallback-fixed")
    badge = (live_badge(f"LIVE · YFINANCE · μ={mu*100:+.1f}% σ={sigma*100:.1f}%")
             if source_label == "yfinance-60d"
             else demo_badge(f"FIXED · μ=8% σ=25%"))

    # Notional: live spot × 100 contracts, rounded to nearest $100
    start_value = max(1000.0, round(float(notional_signature) * 100.0 / 100.0) * 100.0)

    st.markdown(
        terminal_header(
            f"vince · /var SPX · Monte Carlo GBM · ${start_value/1000:,.0f}K book",
            badge,
        ),
        unsafe_allow_html=True,
    )

    controls = st.columns([1, 1, 1, 1])
    with controls[0]:
        n_label = st.radio(
            "Paths", [1000, 10000, 100000],
            index=[1000, 10000, 100000].index(safe_get(st, "mc_n_paths", 10000)),
            format_func=lambda v: f"{v//1000}K" if v < 1_000_000 else "100K",
            horizontal=True, label_visibility="collapsed",
        )
    with controls[1]:
        h_label = st.radio(
            "Horizon", [5, 20, 60],
            index=[5, 20, 60].index(safe_get(st, "mc_horizon", 20)),
            format_func=lambda v: f"{v}D",
            horizontal=True, label_visibility="collapsed",
        )
    with controls[2]:
        if st.button("▶ Run (re-seed)", type="primary", width='stretch'):
            st.session_state.mc_seed = int(np.random.default_rng().integers(0, 2**31 - 1))
    with controls[3]:
        st.markdown(
            f'<div style="text-align:right;font-family:JetBrains Mono,monospace;'
            f'font-size:0.6rem;color:rgba(255,255,255,0.45);text-transform:uppercase;'
            f'letter-spacing:0.12em;padding-top:6px;">'
            f'Seed {safe_get(st, "mc_seed", 42) % 100000} · '
            f'μ={mu*100:+.2f}% · σ={sigma*100:.2f}% · $${start_value/1000:,.0f}K start</div>',
            unsafe_allow_html=True,
        )

    if n_label != safe_get(st, "mc_n_paths", 10000):
        st.session_state.mc_n_paths = n_label
    if h_label != safe_get(st, "mc_horizon", 20):
        st.session_state.mc_horizon = h_label

    result = run_mc(safe_get(st, "mc_n_paths", 10000), safe_get(st, "mc_horizon", 20),
                    mu=mu, sigma=sigma, start_value=start_value,
                    seed=safe_get(st, "mc_seed", 42))

    horizon = safe_get(st, "mc_horizon", 20)
    days = np.arange(0, horizon + 1)

    fig = go.Figure()

    sample = result["sample_paths"]
    for path in sample:
        is_up = path[-1] >= start_value
        fig.add_trace(go.Scatter(
            x=days, y=path, mode="lines",
            line=dict(color=("#34d399" if is_up else "#fb7185"), width=0.8),
            opacity=0.10,
            hoverinfo="skip", showlegend=False,
        ))

    fig.add_trace(go.Scatter(
        x=days, y=result["p95"], mode="lines",
        line=dict(color="#22d3ee", width=1.4, dash="dot"),
        name="P95", hovertemplate="Day %{x}<br>$%{y:,.0f}<extra>P95</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=days, y=result["p5"], mode="lines",
        line=dict(color="#fb7185", width=1.4, dash="dot"),
        name="P5", hovertemplate="Day %{x}<br>$%{y:,.0f}<extra>P5</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=days, y=result["p50"], mode="lines",
        line=dict(color="#69f0ae", width=2.4),
        name="Median",
        hovertemplate="Day %{x}<br>$%{y:,.0f}<extra>Median</extra>",
    ))

    fig.add_hline(
        y=start_value, line=dict(color="rgba(255,255,255,0.25)", width=1, dash="dot"),
        annotation_text=f"${start_value/1000:,.0f}K start",
        annotation_position="right",
        annotation_font=dict(color="rgba(255,255,255,0.5)", family="JetBrains Mono", size=9),
    )

    fig.update_xaxes(
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, color="#8090b0",
        tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        title=dict(text="Trading days",
                   font=dict(color="rgba(255,255,255,0.55)", family="JetBrains Mono", size=10)),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, color="#8090b0",
        tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        tickformat="$,.0f",
        title=dict(text="Portfolio value",
                   font=dict(color="rgba(255,255,255,0.55)", family="JetBrains Mono", size=10)),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=12, r=12, t=12, b=12),
        height=380,
        font=dict(family="JetBrains Mono"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0,
            font=dict(family="JetBrains Mono", size=10, color="#8090b0"),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="mc_bands")

    def fmt_money_signed(x: float) -> str:
        abs_v = abs(x)
        prefix = "-" if x < 0 else "+"
        if abs_v >= 1e6:
            return f"{prefix}${abs_v/1e6:.2f}M"
        if abs_v >= 1e3:
            return f"{prefix}${abs_v/1e3:.1f}K"
        return f"{prefix}${abs_v:,.0f}"

    mr = result["median_return_pct"]
    cards = [
        ("VaR 95%",  fmt_money_signed(-result["var95"]),
         "#34d399" if -result["var95"] >= 0 else "#fb7185"),
        ("CVaR 95%", fmt_money_signed(-result["cvar95"]),
         "#34d399" if -result["cvar95"] >= 0 else "#fb7185"),
        ("Median Return", f"{mr:+.1f}%",
         "#34d399" if mr >= 0 else "#fb7185"),
        ("Worst Case",   f"${result['worst']:,.0f}", "#fb7185"),
    ]
    cols = st.columns(4)
    for col, (label, val, color) in zip(cols, cards):
        col.markdown(
            f'<div class="vc-card" style="padding:10px 12px;margin:0;">'
            f'<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
            f'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.45);">{label}</div>'
            f'<div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;'
            f'font-size:0.95rem;font-weight:600;color:{color};margin-top:4px;">{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render(spot_default: float = 100.0, chain=None) -> None:
    """Top-level entry — strategy calculator + integrated Monte Carlo."""
    render_strategy_calculator(spot_default=spot_default)
    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
    render_monte_carlo(spot_default=spot_default)

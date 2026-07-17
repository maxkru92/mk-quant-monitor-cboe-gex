"""
Greeks Calculator tab — REAL DATA
=================================

Ports ``greeks-calculator.tsx``. Reuses the existing
``greeks.black_scholes_greeks`` engine (scipy-based) for the math, and
renders Plotly payoff diagram + colour-coded greek cards matching the
TSX design.

REAL DATA WIRING (2026-07 Krupp Capital refresh):
- Default σ seeded from the live CBOE chain's ATM call at the nearest
  future-with-positive-OI expiry (``data.cboe_data.get_atm_iv``).
- All other inputs (S, K, T, r) are still slider-driven for the
  scenario-analysis playground.

Inputs (sliders):
- Spot price S, Strike K, Time to expiry T, Risk-free rate r, Vol σ
- Type: Call / Put toggle (matches TSX)

Output:
- Big price card + 5 greek cards (Δ, Γ, ν, Θ, ρ) + moneyness summary
- Payoff at expiry (Plotly line + profit/loss area + breakeven marker)

Real-ticker safe: ``spot_default`` is clamped to the slider range, K is
seeded to ``round(spot)`` (ATM) so SPX/NDX/BRK.A don't crash the slider.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.greeks import black_scholes_greeks
from cboe_menthorq_dashboard.ui.chrome import terminal_header, live_badge, demo_badge
from cboe_menthorq_dashboard.data import cboe_data


# ------------------------------------------------------------------ # \
# Render
# ------------------------------------------------------------------ #
def render(spot_default: float = 100.0, chain=None) -> None:
    initial_spot = max(1.0, min(float(spot_default), 50000.0))
    initial_k = float(int(round(initial_spot))) if initial_spot >= 1.0 else 100.0

    # REAL σ from CBOE ATM call at nearest-with-OI expiry; fallback 30 %.
    initial_sigma = float(cboe_data.get_atm_iv(chain, initial_spot)) if chain is not None else 0.30
    sigma_source = "cboe" if (chain is not None and initial_sigma > 0) else "fallback"

    for k, v in {
        "gk_S":     initial_spot,
        "gk_K":     initial_k,
        "gk_T":     0.25,
        "gk_r":     0.04,
        "gk_sigma": initial_sigma,
        "gk_type":  "call",
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

    badge_html = (live_badge(f"LIVE · ATM IV {initial_sigma*100:.1f}%")
                  if sigma_source == "cboe"
                  else demo_badge("FALLBACK · 30% SIGMA"))

    st.markdown(
        terminal_header(
            "vince · /greeks · Black-Scholes-Merton",
            badge_html,
        ),
        unsafe_allow_html=True,
    )

    left, right = st.columns([2, 3], gap="large")

    color_map = {
        "S":     ("#34d399", "Spot"),
        "K":     ("#22d3ee", "Strike"),
        "T":     ("#fbbf24", "Expiry"),
        "r":     ("#a78bfa", "Rate"),
        "sigma": ("#fb7185", "Vol"),
    }

    with left:
        sliders = [
            ("S",     "Spot Price",     1.0, 50000.0, 1.0,
             lambda v: f"${v:,.2f}"),
            ("K",     "Strike",         1.0, 50000.0, 1.0,
             lambda v: f"${v:,.2f}"),
            ("T",     "Time to Expiry", 0.005, 5.0, 0.01,
             lambda v: f"{v:.2f}y · {int(round(v*365))}d"),
            ("r",     "Risk-free Rate", 0.0, 0.20, 0.001,
             lambda v: f"{v*100:.2f}%"),
            ("sigma", "Volatility",     0.01, 2.0, 0.01,
             lambda v: f"{v*100:.1f}%"),
        ]
        for sym, label, lo, hi, step, fmtfn in sliders:
            color, _ = color_map[sym]
            st.markdown(
                f"""
<div style="display:flex;justify-content:space-between;
            font-family:JetBrains Mono,monospace;font-size:0.55rem;
            text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.4);">
  <span>{label} <span style="font-weight:700;color:{color};">{sym}</span></span>
</div>
""",
                unsafe_allow_html=True,
            )
            val = st.slider(
                sym,
                min_value=float(lo), max_value=float(hi), step=float(step),
                value=float(st.session_state[f"gk_{sym}"]),
                key=f"gk_slider_{sym}",
                label_visibility="collapsed",
            )
            st.session_state[f"gk_{sym}"] = float(val)
            st.markdown(
                f"""
<div style="text-align:right;font-family:JetBrains Mono,monospace;
            font-variant-numeric:tabular-nums;font-size:0.95rem;
            font-weight:600;color:{color};">{fmtfn(val)}</div>
""",
                unsafe_allow_html=True,
            )

        toggles = st.columns(2)
        if toggles[0].button(
            "CALL",
            type="primary" if st.session_state.gk_type == "call" else "secondary",
            width='stretch',
        ):
            st.session_state.gk_type = "call"
        if toggles[1].button(
            "PUT",
            type="primary" if st.session_state.gk_type == "put" else "secondary",
            width='stretch',
        ):
            st.session_state.gk_type = "put"

    S = st.session_state.gk_S
    K = st.session_state.gk_K
    T = st.session_state.gk_T
    r = st.session_state.gk_r
    sigma = st.session_state.gk_sigma
    opt_type = st.session_state.gk_type

    g = black_scholes_greeks(S, K, T, r, sigma,
                             option_type=("Call" if opt_type == "call" else "Put"))

    with left:
        st.markdown(
            f"""
<div class="vc-card" style="padding:10px 12px;margin-top:12px;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.40);">
    Intermediate values
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:0.7rem;color:rgba(255,255,255,0.65);margin-top:6px;">
    d₁ = <span style="color:#fff;font-weight:600;">{g['d1']:.4f}</span> &nbsp;·&nbsp;
    d₂ = <span style="color:#fff;font-weight:600;">{g['d2']:.4f}</span> &nbsp;·&nbsp;
    N(d₁) = <span style="color:#fff;font-weight:600;">{(g['delta']):.4f}</span>
  </div>
  <p style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
            color:rgba(255,255,255,0.35);margin:8px 0 0 0;">
    Black-Scholes-Merton · European exercise · continuous yield assumptions.
    σ sourced from {"live CBOE chain ATM call" if sigma_source == "cboe" else "30% loaded default"}.
    Greeks reported per conventional unit (Vega/Rho per 1%, Theta/day).
  </p>
</div>
""",
            unsafe_allow_html=True,
        )

    with right:
        price_str = f"${g['price']:,.2f}"
        price_color = "#34d399" if opt_type == "call" else "#fb7185"
        intrinsic = max(S - K, 0) if opt_type == "call" else max(K - S, 0)
        extrinsic = max(g["price"] - intrinsic, 0)
        st.markdown(
            f"""
<div class="vc-card" style="padding:14px 18px;margin:0;">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;gap:10px;">
    <div>
      <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
                  text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.45);">
        Option Price · {opt_type.title()}
      </div>
      <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
                  font-size:1.8rem;font-weight:700;color:{price_color};margin-top:4px;">
        {price_str}
      </div>
      <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
                  text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.35);margin-top:2px;">
        per share
      </div>
    </div>
    <div style="text-align:right;">
      <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
                  text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.40);">
        Intrinsic / Time
      </div>
      <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
                  font-size:0.8rem;color:rgba(255,255,255,0.7);margin-top:4px;">
        ${intrinsic:,.2f} / ${extrinsic:,.2f}
      </div>
    </div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        symbol_map = {
            "Delta":     ("Δ", "#34d399", "emerald"),
            "Gamma":     ("Γ", "#22d3ee", "cyan"),
            "Vega":      ("ν", "#a78bfa", "violet"),
            "Theta":     ("Θ", "#fbbf24", "amber"),
            "Rho":       ("ρ", "#fb7185", "rose"),
        }
        greek_symbol_color = {
            "emerald": ("rgba(52,211,153,0.06)", "rgba(52,211,153,0.25)"),
            "cyan":    ("rgba(34,211,238,0.06)", "rgba(34,211,238,0.25)"),
            "violet":  ("rgba(167,139,250,0.06)", "rgba(167,139,250,0.25)"),
            "amber":   ("rgba(251,191,36,0.06)", "rgba(251,191,36,0.25)"),
            "rose":    ("rgba(251,113,133,0.06)", "rgba(251,113,133,0.25)"),
        }
        cards = st.columns(3)
        for col, (name, (sym, color, kind)) in zip(cards, symbol_map.items()):
            bg, border = greek_symbol_color[kind]
            value = g[name.lower()]
            desc_lookup = {
                "Delta": "∂V/∂S — spot sensitivity",
                "Gamma": "∂²V/∂S² — delta's rate of change",
                "Vega":  "∂V/∂σ — per 1% vol move",
                "Theta": "∂V/∂t — per-day decay",
                "Rho":   "∂V/∂r — per 1% rate move",
            }
            col.markdown(
                f"""
<div class="vc-card" style="padding:10px 12px;margin:0;background:{bg};border:1px solid {border};">
  <div style="display:flex;justify-content:space-between;">
    <span style="font-family:JetBrains Mono,monospace;font-size:1rem;font-weight:700;color:{color};">{sym}</span>
    <span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.45);">{name}</span>
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;font-size:1.1rem;font-weight:600;color:{color};margin-top:8px;">{value:+.4f}</div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;line-height:1.3;color:rgba(255,255,255,0.45);margin-top:4px;">{desc_lookup[name]}</div>
</div>
""",
                unsafe_allow_html=True,
            )

        money_pct = (S / K - 1) * 100
        if S > K:
            money_label = "ITM" if opt_type == "call" else "OTM"
            money_color = "#34d399"
        elif S < K:
            money_label = "OTM" if opt_type == "call" else "ITM"
            money_color = "#fb7185"
        else:
            money_label = "ATM"
            money_color = "#fbbf24"
        st.markdown(
            f"""
<div class="vc-card" style="padding:10px 12px;margin-top:8px;display:flex;align-items:center;justify-content:space-between;">
  <div>
    <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.40);">Moneyness</div>
    <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;font-size:1.1rem;font-weight:600;color:{money_color};margin-top:4px;">{money_pct:+.2f}%</div>
  </div>
  <span style="font-family:JetBrains Mono,monospace;font-size:0.7rem;font-weight:700;color:{money_color};opacity:0.85;">{money_label}</span>
</div>
""",
            unsafe_allow_html=True,
        )

        _render_payoff(S, K, g["price"], opt_type)


def _render_payoff(S, K, premium, opt_type) -> None:
    start = K * 0.5
    end = round(K * 1.5)
    if end <= start:
        end = start + 1.0
    N = 120

    pts = []
    for i in range(N + 1):
        s = start + (end - start) * i / N
        intrinsic = max(s - K, 0) if opt_type == "call" else max(K - s, 0)
        pts.append({"s": s, "pnl": intrinsic - premium})

    all_pnl = [p["pnl"] for p in pts]
    raw_min = min(all_pnl + [0])
    raw_max = max(all_pnl + [0])
    ypad = (raw_max - raw_min) * 0.08 or 1
    min_pnl, max_pnl = raw_min - ypad, raw_max + ypad
    rng = max_pnl - min_pnl or 1

    def ys(p): return 1.0 - (p - min_pnl) / rng

    x_dollars = [p["s"] for p in pts]
    y_norm    = [ys(p["pnl"]) for p in pts]
    y_zero    = ys(0)

    tick_pct = np.linspace(0.0, 1.0, 6)
    tickvals = [start + p * (end - start) for p in tick_pct]
    ticktext = [f"${v:,.0f}" for v in tickvals]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=[start, *x_dollars, end],
        y=[y_zero, *y_norm, y_zero],
        mode="lines",
        line=dict(width=0),
        fill="toself",
        fillcolor="rgba(52,211,153,0.22)",
        hoverinfo="skip",
        showlegend=False,
    ))

    fig.add_vline(
        x=K,
        line=dict(color="#22d3ee", width=1.2, dash="dot"),
        opacity=0.50,
        annotation_text="K",
        annotation_position="top",
        annotation_font=dict(color="#22d3ee", family="JetBrains Mono", size=10),
    )

    fig.add_trace(go.Scatter(
        x=x_dollars, y=y_norm, mode="lines",
        line=dict(color="#ffffff", width=2.0),
        name="P&L curve",
        customdata=[p["pnl"] for p in pts],
        hovertemplate="Spot %{x:$,.2f}<br>P&amp;L %{customdata:$,.2f}<extra></extra>",
    ))

    be = K + premium if opt_type == "call" else K - premium
    if start <= be <= end:
        fig.add_trace(go.Scatter(
            x=[be], y=[y_zero],
            mode="markers+text",
            marker=dict(color="#fbbf24", size=9, line=dict(color="#14141c", width=1.5)),
            text=[f"  BE ${be:,.1f}"],
            textposition="bottom center",
            textfont=dict(color="#fbbf24", family="JetBrains Mono", size=10),
            showlegend=False, hoverinfo="skip",
        ))

    if start <= S <= end:
        cur_pnl = (max(S - K, 0) if opt_type == "call" else max(K - S, 0)) - premium
        marker_color = "#34d399" if cur_pnl >= 0 else "#fb7185"
        fig.add_vline(
            x=S,
            line=dict(color="rgba(255,255,255,0.55)", width=1.2, dash="dot"),
            annotation_text=f"S=${S:,.0f}",
            annotation_position="top",
            annotation_font=dict(color="#ffffff", family="JetBrains Mono", size=10),
        )
        fig.add_trace(go.Scatter(
            x=[S], y=[ys(cur_pnl)],
            mode="markers",
            marker=dict(color=marker_color, size=10,
                        line=dict(color="#14141c", width=2)),
            hoverinfo="skip", showlegend=False,
        ))

    fig.update_xaxes(
        range=[start, end],
        tickvals=tickvals, ticktext=ticktext,
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
        showticklabels=False,
        ticktext=["—"],
        title=dict(text="P&amp;L ($) — values via hover (B/E marked)",
                   font=dict(color="rgba(255,255,255,0.55)", family="JetBrains Mono", size=10)),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=12, r=12, t=12, b=12),
        height=300,
        font=dict(family="JetBrains Mono"),
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="greeks_payoff")

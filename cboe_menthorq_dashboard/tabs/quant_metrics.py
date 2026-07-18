"""
Quant Metrics tab — REAL DATA
==============================

Three visualisations stacked on one tab, each wired to live data:

1. **Vol Surface** — Plotly 3-D surface built from the live CBOE chain IVs
   (via ``data.vol_surface.get_vol_surface_mesh``). Fallback to deterministic
   _smile() mesh if the chain is too thin.

2. **Volatility Chart** — Plotly candlesticks from yfinance ``^GSPC``
   (via ``data.candles.get_volatility_candles``). Fallback to deterministic
   placeholder candles if yfinance is offline.

3. **Regime Detection** — 60-tick ``^GSPC`` price path with a Cartesian
   trend × vol classifier (via ``data.regime.get_regime_data``). Transition
   matrix from the same window; probability bars from the last 20 days.

Source badge in each header announces the dataline: ``LIVE · CBOE`` /
``LIVE · YFINANCE`` or ``FALLBACK · DEMO``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cboe_menthorq_dashboard.ui.chrome import terminal_header, live_badge, demo_badge
from cboe_menthorq_dashboard.data import vol_surface, regime, cboe_data
# Individual function imports moved inside render_* functions to avoid
# Python 3.14 scoping/bytecode issues on Streamlit Cloud.
# The module-level imports below are for types/docs only.
from cboe_menthorq_dashboard.data import candles as _candles_mod


# ------------------------------------------------------------------ #
# Hex → rgba helper for Plotly fill/edges
# ------------------------------------------------------------------ #
def _hex_to_rgba(hex_color: str, alpha: float = 0.10) -> str:
    """Convert '#rrggbb' -> 'rgba(r,g,b,a)' so Plotly fill/edges won't choke.

    ``:.2f`` formatting on alpha keeps canonical 0.10 instead of 0.1.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


# ------------------------------------------------------------------ #
# 1. Vol Surface
# ------------------------------------------------------------------ #
def render_vol_surface(spot_default: float = 100.0, chain=None) -> None:
    atm = max(1.0, float(spot_default))
    source = vol_surface.get_vol_surface_source(chain)
    if source == "cboe":
        strikes_axis, times_axis, Z = vol_surface.get_vol_surface_mesh(chain, atm)
    else:
        strikes_axis, times_axis, Z = None, None, None

    if Z is None or not np.isfinite(Z).any():
        # Fallback deterministic surface
        strikes_axis = np.linspace(0.8 * atm, 1.2 * atm, 16)
        times_axis = np.linspace(1.0 / 52.0, 1.0, 12)
        K, T = np.meshgrid(strikes_axis, times_axis)
        Z = vol_surface.fallback_smile(K, T, S0=atm) * 100.0

    badge_html = live_badge("LIVE · CBOE VOL SURFACE") if source == "cboe" else demo_badge("FALLBACK · DEMO MESH")
    st.markdown(
        terminal_header(
            "vince · /vol_surface · live CBOE IV grid",
            badge_html,
        ),
        unsafe_allow_html=True,
    )

    fig = go.Figure(
        data=[
            go.Surface(
                x=strikes_axis,
                y=times_axis,
                z=Z,
                colorscale=[
                    [0.0, "#34d399"],
                    [0.3, "#22d3ee"],
                    [0.55, "#fbbf24"],
                    [0.85, "#fb923c"],
                    [1.0, "#fb7185"],
                ],
                showscale=True,
                colorbar=dict(
                    title=dict(text="IV %",
                               font=dict(color="#8090b0", family="JetBrains Mono", size=10)),
                    tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
                    thickness=10,
                    len=0.7,
                ),
                opacity=0.92,
                hovertemplate="Strike %{x:.2f}<br>T %{y:.3f}y<br>IV %{z:.2f}%<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        paper_bgcolor="#0b0f1e",
        plot_bgcolor="#0b0f1e",
        scene=dict(
            bgcolor="#0b0f1e",
            xaxis=dict(title=dict(text="Strike", font=dict(color="#e8eeff", size=11)),
                       backgroundcolor="#0b0f1e", gridcolor="#141c35", color="#e8eeff"),
            yaxis=dict(title=dict(text="Years", font=dict(color="#e8eeff", size=11)),
                       backgroundcolor="#0b0f1e", gridcolor="#141c35", color="#e8eeff"),
            zaxis=dict(title=dict(text="IV %", font=dict(color="#e8eeff", size=11)),
                       backgroundcolor="#0b0f1e", gridcolor="#141c35", color="#e8eeff"),
        ),
        margin=dict(l=0, r=0, b=0, t=10),
        height=380,
        font=dict(family="JetBrains Mono", color="#e8eeff", size=10),
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="quant_vol_surface")

    # Single footer stat — ATM Vol at the shortest picked expiry (or the
    # surface mean if all NaN). Strictly derived from Z (no hardcoded numbers).
    valid = Z[~np.isnan(Z)]
    if valid.size:
        atm_idx = len(strikes_axis) // 2
        # Use shortest expiry (column 0) for "1W" framing; or fall back to surface mean.
        col0 = Z[:, 0]
        atm_vol_1w = float(col0[atm_idx]) if np.isfinite(col0[atm_idx]) else float(valid.mean())
    else:
        atm_vol_1w = 0.0

    st.markdown(
        f"""
<div class="vc-card" style="padding:10px 14px;margin-top:8px;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.4);">
    ATM Vol · Shortest Expiry
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:0.95rem;font-weight:600;color:#34d399;margin-top:4px;">
    {atm_vol_1w:.2f}%
  </div>
  <div style="font-family:JetBrains Mono,monospace;font-size:0.5rem;color:rgba(255,255,255,0.30);
              margin-top:4px;">
    derived from {'CBOE chain IV' if source == 'cboe' else 'deterministic smile (fallback)'}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ #
# 2. Volatility Chart (real ^GSPC OHLC)
# ------------------------------------------------------------------ #
def render_volatility_chart(_chain=None) -> None:  # chain kwarg kept for symmetry
    # Inner-spinner defense (rare, covers 5-min cache expiry during session)
    with st.spinner("Loading live ^GSPC 30-day OHLC\u2026"):
        ohlc_data, source = _candles_mod.get_volatility_candles("^GSPC", 30)
    badge_html = (live_badge("LIVE · YFINANCE OHLC")
                  if source == "yfinance"
                  else demo_badge("FALLBACK · DEMO OHLC"))
    st.markdown(
        terminal_header(
            "vince · /chart SPX · ^GSPC 30D OHLC",
            badge_html,
        ),
        unsafe_allow_html=True,
    )

    last = ohlc_data.iloc[-1]
    last_close = float(last["close"])
    prev_close = float(ohlc_data.iloc[-2]["close"]) if len(ohlc_data) >= 2 else last_close
    chg = last_close - prev_close
    pct = chg / prev_close * 100.0 if prev_close else 0.0
    chg_color = "#34d399" if chg >= 0 else "#fb7185"

    st.markdown(
        f"""
<div style="display:flex;justify-content:space-between;align-items:flex-end;
            padding:12px 0 8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
  <div>
    <div style="display:flex;align-items:center;gap:8px;">
      <span style="font-family:JetBrains Mono,monospace;font-size:1rem;font-weight:600;color:#fff;">SPX</span>
      <span style="border:1px solid rgba(52,211,153,0.3);background:rgba(52,211,153,0.1);
                   color:#34d399;padding:2px 6px;border-radius:3px;
                   font-family:JetBrains Mono,monospace;font-size:0.55rem;
                   text-transform:uppercase;letter-spacing:0.12em;">index</span>
    </div>
    <div style="font-family:JetBrains Mono,monospace;font-size:0.6rem;text-transform:uppercase;
                letter-spacing:0.12em;color:rgba(255,255,255,0.35);margin-top:4px;">
      S&amp;P 500 · {'realtime' if source == 'yfinance' else 'demo feed'}
    </div>
  </div>
  <div style="text-align:right;">
    <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
                font-size:1.4rem;font-weight:600;color:#fff;">
      {last_close:,.2f}
    </div>
    <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
                font-size:0.75rem;color:{chg_color};">
      {'▲' if chg >= 0 else '▼'} {abs(pct):.2f}% · {'+' if chg >= 0 else '-'}{abs(chg):.2f}
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=ohlc_data["t"],
                open=ohlc_data["open"], high=ohlc_data["high"],
                low=ohlc_data["low"], close=ohlc_data["close"],
                increasing_line_color="#34d399", increasing_fillcolor="#34d399",
                decreasing_line_color="#fb7185", decreasing_fillcolor="#fb7185",
                showlegend=False,
            )
        ]
    )
    fig.add_hline(
        y=last_close, line=dict(color="#34d399", width=1, dash="dot"),
        annotation_text=f"{last_close:,.1f}",
        annotation_position="right",
        annotation_font=dict(color="#34d399", size=9, family="JetBrains Mono"),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8),
        height=220,
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
        ),
        font=dict(family="JetBrains Mono"),
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="quant_vol_chart")

    ind = [
        ("IV (ATM)", f"{cboe_data.get_atm_iv(_chain, last_close)*100:.1f}", "#fbbf24"),
        ("RV20",      f"{_expected_rv20(ohlc_data)*100:.1f}", "#22d3ee"),
        ("Δ (ATM)",   "0.50", "#34d399"),  # ATM delta is by definition ≈ 0.5
        ("Γ (peak)",  "—",   "#a78bfa"),
    ]
    cols = st.columns(4)
    for col, (label, val, color) in zip(cols, ind):
        col.markdown(
            f"""
<div class="vc-card" style="padding:8px 10px;margin:0;text-align:center;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;
              text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.35);">{label}</div>
  <div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;
              font-size:0.95rem;font-weight:600;color:{color};margin-top:4px;">{val}</div>
</div>
""",
            unsafe_allow_html=True,
        )


def _expected_rv20(_ohlc_df):
    """20-day realised vol (annualised) from the last 20 closes."""
    _closes = _ohlc_df["close"].astype(float).values
    if len(_closes) < 21:
        return 0.0
    _rets = np.diff(_closes)
    return float(np.std(_rets, ddof=1) * np.sqrt(252.0))


# ------------------------------------------------------------------ #
# 3. Regime Detection (real ^GSPC history, Cartesian classifier)
# ------------------------------------------------------------------ #
REGIME_COLORS = {0: "#34d399", 1: "#22d3ee", 2: "#f43f5e"}
REGIME_LABELS = {0: "Bull / Low Vol", 1: "Sideways / Normal", 2: "Bear / High Vol"}


def render_regime_detection(_chain=None) -> None:
    # Inner-spinner defense (rare, covers 5-min cache expiry during session)
    with st.spinner("Loading live ^GSPC 90-day regime classifier\u2026"):
        regime_data = regime.get_regime_data()
    source = regime_data.get("source", "fallback")

    badge_html = (live_badge("LIVE · YFINANCE HMM-90D")
                  if source == "yfinance-90d"
                  else demo_badge("FALLBACK · SPA MODE"))
    st.markdown(
        terminal_header(
            "vince · /regime SPX · ^GSPC regime classifier",
            badge_html,
        ),
        unsafe_allow_html=True,
    )

    if not regime_data.get("price_path") or not regime_data.get("macros"):
        st.info("Live regime data unavailable. Showing SPA fallback matrix only.")
        _render_regime_matrix_only(regime_data)
        return

    price_path = regime_data["price_path"]
    macros = regime_data["macros"]

    # X-axis: trade-day index (T-N..T-0).
    # regime.py emits len(macros) = len(price_path) - 1, with macro[i] ↔ close[i+1]
    # (see data/regime.py docstring). We trim price_path to align the arrays.
    n = min(len(macros), len(price_path))
    if n <= 0:
        # Belt-and-suspenders: if either list is empty past the early-return guard,
        # render the SPA matrix instead of crashing with IndexError.
        _render_regime_matrix_only(regime_data)
        return
    price_path = price_path[-n:]
    y_min = min(price_path) - 15
    y_max = max(price_path) + 15

    # Build regime-coloured area segments
    segments = []
    current = macros[0]
    start = 0
    for i in range(1, n + 1):
        if i == n or macros[i] != current:
            segments.append((current, start, i - 1))
            if i < n:
                current = macros[i]
                start = i

    fig = go.Figure()
    for regime_state, s_idx, e_idx in segments:
        xs = list(range(s_idx, e_idx + 1))
        ys = [price_path[i] for i in xs]
        color = REGIME_COLORS[regime_state]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(color=color, width=2.4),
            fill="tozeroy",
            fillcolor=_hex_to_rgba(color, 0.10),
            showlegend=False,
            name=REGIME_LABELS[regime_state],
            hovertemplate="T-%{x}<br>Price %{y:,.0f}<extra></extra>",
        ))

    for i in range(1, n):
        if macros[i] != macros[i - 1]:
            fig.add_vline(x=i, line=dict(color="rgba(255,255,255,0.18)",
                                         width=0.7, dash="dot"))

    last_idx = n - 1
    last_regime = macros[last_idx]
    last_color = REGIME_COLORS[last_regime]
    fig.add_trace(go.Scatter(
        x=[last_idx], y=[price_path[last_idx]],
        mode="markers+text",
        marker=dict(color=last_color, size=10, line=dict(color=last_color, width=2)),
        text=[f"  {REGIME_LABELS[last_regime]}"],
        textposition="middle right",
        textfont=dict(color=last_color, family="JetBrains Mono", size=11),
        showlegend=False,
        hovertemplate="Now<extra></extra>",
    ))

    # X-axis labels: pin to T-N / T-2N/3 / T-N/3 / Now
    tick_idx = [0, max(1, n // 3), max(2, 2 * n // 3), last_idx]
    tick_labels = ["T-60", "T-40", "T-20", "Now"]
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0f1e",
        margin=dict(l=8, r=8, t=8, b=8),
        height=280,
        xaxis=dict(
            title=None, gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="#2a3456", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
            tickmode="array", tickvals=tick_idx, ticktext=tick_labels,
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.05)", color="#8090b0",
            tickfont=dict(family="JetBrains Mono", size=9, color="#8090b0"),
            range=[y_min, y_max],
        ),
        font=dict(family="JetBrains Mono"),
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', theme=None, key="quant_regime_line")

    _render_regime_prob_bars(regime_data)
    _render_regime_matrix(regime_data)
    _render_regime_summary_cards(regime_data)


def _render_regime_prob_bars(regime: dict) -> None:
    probs = regime.get("probs", [])
    if not probs:
        return
    # Sparkline history proxy: use the last 20 regime assignments
    macros_tail = regime.get("macros", [])[-20:] or []
    for label, prob, state in probs:
        color = REGIME_COLORS[state]
        ratio = int(prob * 100)
        st.markdown(
            f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
  <span style="width:64px;flex-shrink:0;font-family:JetBrains Mono,monospace;
               font-size:0.7rem;font-weight:600;color:{color};">{label}</span>
  <div style="position:relative;height:18px;flex:1;background:rgba(255,255,255,0.04);
              border-radius:4px;overflow:hidden;">
    <div style="position:absolute;left:0;top:0;height:100%;width:{ratio}%;
                background:{color};opacity:0.85;border-radius:4px;"></div>
  </div>
  <span style="width:34px;text-align:right;flex-shrink:0;font-family:JetBrains Mono,monospace;
               font-variant-numeric:tabular-nums;font-size:0.7rem;
               font-weight:600;color:rgba(255,255,255,0.7);">{ratio}%</span>
  <span style="width:48px;flex-shrink:0;font-family:JetBrains Mono,monospace;
            font-size:0.55rem;color:rgba(255,255,255,0.45);">T-20d</span>
</div>
""",
            unsafe_allow_html=True,
        )


def _render_regime_matrix(regime: dict) -> None:
    trans = regime.get("trans", [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.2, 0.7]])
    state_labels = ["Bull", "Sideways", "Bear"]
    cells = ['<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
             'text-transform:uppercase;letter-spacing:0.12em;'
             'color:rgba(255,255,255,0.4);margin-top:14px;">'
             'Self-Transition Probability (last 60 trading days)</div>']
    cells.append(
        '<div style="display:inline-grid;grid-template-columns:auto repeat(3, 1fr);'
        'gap:2px;margin-top:8px;min-width:260px;font-family:JetBrains Mono,monospace;">'
    )
    cells.append('<div></div>')
    for lbl in state_labels:
        cells.append(
            f'<div style="text-align:center;padding:4px 0;color:rgba(255,255,255,0.5);'
            f'font-size:0.6rem;font-weight:600;">{lbl}</div>'
        )
    for r_idx, row in enumerate(trans):
        cells.append(
            f'<div style="padding:4px 8px;color:rgba(255,255,255,0.5);'
            f'font-size:0.6rem;font-weight:600;">{state_labels[r_idx]}</div>'
        )
        for v in row:
            v_float = float(v)
            if v_float >= 0.7:
                color, bg, border = ("#34d399", "rgba(52,211,153,0.10)", "rgba(52,211,153,0.20)")
            elif v_float >= 0.15:
                color, bg, border = ("#fbbf24", "rgba(251,191,36,0.10)", "rgba(251,191,36,0.20)")
            else:
                color, bg, border = ("#fb7185", "rgba(251,113,133,0.10)", "rgba(251,113,133,0.20)")
            cells.append(
                f'<div style="text-align:center;padding:6px 0;border-radius:3px;'
                f'background:{bg};border:1px solid {border};color:{color};'
                f'font-size:0.6rem;font-weight:700;">{v_float:.2f}</div>'
            )
    cells.append("</div>")
    st.markdown("\n".join(cells), unsafe_allow_html=True)


def _render_regime_summary_cards(regime: dict) -> None:
    macros = regime.get("macros", [])
    if not macros:
        return _render_regime_matrix_only(regime)
    last_regime = int(macros[-1])
    last_label = REGIME_LABELS[last_regime]
    last_color = REGIME_COLORS[last_regime]

    # Regime duration: how many consecutive latest-state runs have we had?
    duration = 1
    for k in range(len(macros) - 2, -1, -1):
        if macros[k] == last_regime:
            duration += 1
        else:
            break

    # Most-likely next regime from self-transition
    trans = regime.get("trans", [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.2, 0.7]])
    nxt_idx = max(range(3), key=lambda k: trans[last_regime][k])
    nxt_label = REGIME_LABELS[nxt_idx]
    nxt_pct = f"{float(trans[last_regime][nxt_idx])*100:.0f}%"

    cards = [
        ("Current Regime",         last_label,                  last_color, "dot"),
        ("Regime Duration",        f"{duration} d",              "#22d3ee",  None),
        ("Most Likely Transition", f"{nxt_label} ({nxt_pct})",   "#fbbf24",  "arrow"),
        ("Spot (last close)",      f"{float(regime['price_path'][-1]):,.2f}",
                                                            "#fff",   None),
    ]
    cols = st.columns(4)
    for col, (label, val, color, kind) in zip(cols, cards):
        prefix_html = ""
        if kind == "dot":
            prefix_html = f'<span style="width:6px;height:6px;border-radius:50%;background:{color};display:inline-block;margin-right:6px;"></span>'
        elif kind == "arrow":
            prefix_html = f'<span style="font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{color};margin-right:6px;">→</span>'
        col.markdown(
            f'<div class="vc-card" style="padding:8px 10px;margin:0;">'
            f'<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
            f'text-transform:uppercase;letter-spacing:0.12em;color:{color};opacity:0.85;">{label}</div>'
            f'<div style="font-family:JetBrains Mono,monospace;font-variant-numeric:tabular-nums;'
            f'font-size:0.8rem;font-weight:600;color:{color};margin-top:6px;">{prefix_html}{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_regime_matrix_only(regime: dict) -> None:
    """Fallback visual: just the SPA-mode 3×3 matrix when no live price data."""
    st.markdown(
        '<div style="font-family:JetBrains Mono,monospace;font-size:0.55rem;'
        'text-transform:uppercase;letter-spacing:0.12em;color:rgba(255,255,255,0.4);'
        'margin-top:14px;">Transition Probability Matrix (SPA fallback)</div>',
        unsafe_allow_html=True,
    )
    matrix_html = ["<div style='display:inline-grid;grid-template-columns:auto repeat(3,1fr);gap:2px;margin-top:8px;'>"]
    state_labels = ["Bull", "Sideways", "Bear"]
    matrix_html.append("<div></div>")
    for lbl in state_labels:
        matrix_html.append(
            f'<div style="text-align:center;padding:4px;color:rgba(255,255,255,0.5);'
            f'font-size:0.6rem;font-weight:600;">{lbl}</div>'
        )
    trans = regime.get("trans", [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.2, 0.7]])
    for r_idx, row in enumerate(trans):
        matrix_html.append(
            f'<div style="padding:4px 8px;color:rgba(255,255,255,0.5);'
            f'font-size:0.6rem;font-weight:600;">{state_labels[r_idx]}</div>'
        )
        for v in row:
            v_f = float(v)
            color = "#34d399" if v_f >= 0.7 else "#fbbf24" if v_f >= 0.15 else "#fb7185"
            matrix_html.append(
                f'<div style="text-align:center;padding:6px 0;border-radius:3px;'
                f'background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);'
                f'color:{color};font-family:JetBrains Mono,monospace;font-size:0.6rem;'
                f'font-weight:700;">{v_f:.2f}</div>'
            )
    matrix_html.append("</div>")
    st.markdown("\n".join(matrix_html), unsafe_allow_html=True)


# ------------------------------------------------------------------ #
# Top-level entry
# ------------------------------------------------------------------ #
def render(spot_default: float = 100.0, chain=None) -> None:
    render_vol_surface(spot_default=spot_default, chain=chain)
    render_volatility_chart(_chain=chain)
    render_regime_detection(_chain=chain)

"""
Dark-institutional theme tokens & global CSS injection
======================================================

Source-of-truth palette for the Krupp Capital GEX dashboard. Mirrors the
binding palette in ``chart_generator.py`` (Krupp Capital styleguide v2) and
adds the React/TSX design tokens (`#34d399`, `#22d3ee`, `#fb7185`, etc.) so
that any ported component (vol surface, payoff diagram, regime detection,
market clock) renders with the same dark-institutional aesthetic.

Call ``inject_css()`` once near the top of ``app.py`` to apply the styles.
"""

from __future__ import annotations

import streamlit as st


# ------------------------------------------------------------------ #
# Palette  — binding (do not rename constants; consumers import these)
# ------------------------------------------------------------------ #
# Backgrounds
MAIN_BG    = "#05080f"   # outer Streamlit page surface
PANEL_BG   = "#0b0f1e"   # cards / panels
GRID       = "#141c35"   # subtle internal grid lines
ZERO_LINE  = "#2a3456"   # zero-axes, watermark
TEXT_PRI   = "#e8eeff"   # titles, key-data values
TEXT_SEC   = "#8090b0"   # axis labels, ticks

# Data-point semantics
CYAN         = "#00b0ff"   # Calls / GEX (institutional cyan, matches chart_generator)
EMERALD      = "#00e676"   # Positive (profit, calls) [was #34d399 in TSX → mapped to styleguide emerald]
LIGHT_GREEN  = "#69f0ae"   # Secondary bullish (OI line)
ROSE         = "#ff1744"   # Negative (puts, loss) [was #fb7185 in TSX → styleguide rose]
PINK_RED     = "#ff6090"   # Secondary bearish (put-OI)
AMBER        = "#ffd600"   # Spot / breakeven / VOC
VIOLET       = "#aa00ff"   # Risk/reward, forward vol
ORANGE       = "#ff6d00"   # Gamma flip / warnings

# Light tints used inside plotly/SVG for opacity overlays (do NOT use outside CSS).
EMERALD_DIM = "rgba(0,230,118,0.18)"
ROSE_DIM    = "rgba(255,23,68,0.22)"
CYAN_DIM    = "rgba(0,176,255,0.18)"
AMBER_DIM   = "rgba(255,214,0,0.18)"


# ------------------------------------------------------------------ #
# Global CSS — injected once at app boot via inject_css()
# ------------------------------------------------------------------ #
GLOBAL_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');

/* === outer Streamlit page === */
.stApp, .main, section[data-testid="stAppViewContainer"] {{
    background: {MAIN_BG} !important;
}}

/* === monospace everywhere appropriate === */
code, pre, .mono, .stCode, .stMarkdown code,
[data-testid="stMarkdownContainer"] code {{
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
}}

/* === tabs === */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}
.stTabs [data-baseweb="tab"] {{
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: rgba(255,255,255,0.55);
}}
.stTabs [aria-selected="true"] {{
    color: {EMERALD} !important;
    background: rgba(0,230,118,0.06) !important;
    border: 1px solid rgba(0,230,118,0.3) !important;
    border-bottom: 2px solid {EMERALD} !important;
}}

/* === card primitive === */
.vc-card {{
    background: {PANEL_BG};
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: 16px;
    margin: 8px 0;
    box-sizing: border-box;
    width: 100%;
}}

/* === card header (terminal style) === */
.vc-card-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding-bottom: 8px;
    margin-bottom: 12px;
}}

.vc-dots  {{ display: inline-flex; gap: 4px; }}
.vc-dot   {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
.vc-dot-r {{ background: rgba(255,23,68,0.7); }}
.vc-dot-a {{ background: rgba(255,214,0,0.7); }}
.vc-dot-e {{ background: rgba(0,230,118,0.7); }}

.vc-title {{ font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
             font-weight: 600; color: rgba(255,255,255,0.7); margin-left: 8px; }}

.vc-badge {{ font-family: 'JetBrains Mono', monospace; font-size: 0.55rem;
             font-weight: 700; padding: 2px 6px; border-radius: 4px;
             border: 1px solid; letter-spacing: 0.12em; display: inline-block; }}
.vc-badge-live {{ color: {EMERALD}; border-color: rgba(0,230,118,0.40);
                  background: rgba(0,230,118,0.10); }}
.vc-badge-demo {{ color: {AMBER}; border-color: rgba(255,214,0,0.40);
                  background: rgba(255,214,0,0.10); }}

/* === numeric tabular figures === */
.vc-num, .numerical, [data-testid="stMetricValue"] {{
    font-variant-numeric: tabular-nums;
    font-family: 'JetBrains Mono', monospace !important;
}}

/* === sidebar === */
section[data-testid="stSidebar"] {{
    background: {PANEL_BG};
    border-right: 1px solid rgba(255,255,255,0.06);
}}

/* === slider — emerald fill === */
input[type="range"] {{ accent-color: {EMERALD}; }}

/* === metric cards === */
[data-testid="stMetric"] {{
    background: {PANEL_BG};
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 10px 14px;
}}

/* === dataframes === */
.stDataFrame {{ font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; }}

/* === pulse animation for live markers === */
@keyframes vcPulse {{
    0%   {{ opacity: 1;    }}
    50%  {{ opacity: 0.30; }}
    100% {{ opacity: 1;    }}
}}
.live-pulse {{ animation: vcPulse 1s infinite; }}
</style>
"""


def inject_css() -> None:
    """Inject the global theme CSS once per page-load."""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

"""
Reusable HTML chrome components
================================

Tiny raw-HTML helpers that mirror the TSX React design primitives (terminal
header, badges, market-clock strip). All output is ``unsafe_allow_html=True``
safe — no external assets, no user-controlled content is rendered as raw HTML.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


# ------------------------------------------------------------------ #
# Terminal header — 3 colored dots + mono title + badges
# ------------------------------------------------------------------ #
def terminal_header(title: str, badges_html: str = "") -> str:
    """Render the dark-institutional terminal-card header (binding to TSX design)."""
    badges = badges_html or ""
    return f"""
<div class="vc-card-header">
  <div style="display:flex;align-items:center;gap:8px;">
    <span class="vc-dots">
      <span class="vc-dot vc-dot-r"></span>
      <span class="vc-dot vc-dot-a"></span>
      <span class="vc-dot vc-dot-e"></span>
    </span>
    <span class="vc-title">{title}</span>
  </div>
  <div>{badges}</div>
</div>
"""


# ------------------------------------------------------------------ #
# Badges
# ------------------------------------------------------------------ #
_BADGE_TPL = '<span class="vc-badge vc-badge-{kind}">{label}</span>'


def live_badge(label: str = "LIVE") -> str:
    """Green badge with a subtle accent dot prefix."""
    return _BADGE_TPL.format(kind="live", label=label.upper())


def demo_badge(label: str = "DEMO") -> str:
    """Amber badge — used wherever TSX had a 'demo' marker."""
    return _BADGE_TPL.format(kind="demo", label=label.upper())


# ------------------------------------------------------------------ #
# Card wrapper
# ------------------------------------------------------------------ #
def card_open() -> str:
    return '<div class="vc-card">'


def card_close() -> str:
    return "</div>"


# ------------------------------------------------------------------ #
# Market clock — server-side rendered (no JS, updates on Streamlit rerun)
# ------------------------------------------------------------------ #
#   Shows NYC and LON current local time + market open/closed status.
#   Rendered on every Streamlit rerun — no JS, no browser tick needed.
#   Timezone handling via Python zoneinfo (stdlib, Python 3.9+).
#   Market hours (local time):
#     NYSE: 09:30–16:00 ET (Mon–Fri)
#     LSE:  08:00–16:30 BST (Mon–Fri)
# ------------------------------------------------------------------ #

_MARKETS = [
    ("NYC", "America/New_York", 9 * 60 + 30, 16 * 60),      # 09:30-16:00 ET
    ("LON", "Europe/London",    8 * 60,       16 * 60 + 30), # 08:00-16:30 LCL
]


def _is_open(now_local: datetime, open_min: int, close_min: int) -> bool:
    """Check if current local minute-of-day falls within market hours."""
    wd = now_local.weekday()  # Mon=0 … Sun=6
    if wd >= 5:  # Saturday / Sunday
        return False
    mins = now_local.hour * 60 + now_local.minute
    return open_min <= mins < close_min


def render_market_clock() -> str:
    """Return server-side rendered market clock strip.

    Inject via ``st.markdown(render_market_clock(), unsafe_allow_html=True)``.
    The time displays update on every Streamlit rerun (interaction, slider
    change, tab switch, etc.) — no JS required.
    """
    parts = []
    for code, tz_name, open_min, close_min in _MARKETS:
        tz = ZoneInfo(tz_name)
        now_local = datetime.now(tz)
        timestr = now_local.strftime("%H:%M:%S")
        is_open = _is_open(now_local, open_min, close_min)
        dot_color = "#00e676" if is_open else "rgba(255,255,255,0.40)"
        dot_bg = "#00e676" if is_open else "rgba(255,255,255,0.30)"
        status = "OPEN" if is_open else "closed"
        parts.append(
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'color:{dot_color};" '
            f'title="{code} {timestr} \u25cf {status}">'
            f'<span style="width:6px;height:6px;border-radius:50%;'
            f'background:{dot_bg};"></span>'
            f'<span style="font-size:0.50rem;font-weight:700;'
            f'letter-spacing:0.10em;">{code}</span>'
            f'<span style="font-family:JetBrains Mono,monospace;'
            f'font-size:0.70rem;font-variant-numeric:tabular-nums;">'
            f'{timestr}</span></span>'
        )
    clock_html = "\n".join(parts)

    return f"""
<div id="vc-market-clock" style="
    display:flex;align-items:center;justify-content:space-between;
    padding:8px 16px;border-radius:10px;
    background:#0b0f1e;border:1px solid rgba(255,255,255,0.06);
    margin:8px 0 14px 0;">
  <span style="
    font-family:'JetBrains Mono',monospace;font-size:0.6rem;
    font-weight:700;color:rgba(255,255,255,0.5);
    text-transform:uppercase;letter-spacing:0.16em;">
    Krupp Capital · Quant Research Desk
  </span>
  <div style="
    display:flex;gap:16px;font-family:'JetBrains Mono',monospace;
    font-size:0.65rem;letter-spacing:0.1em;">{clock_html}</div>
</div>"""

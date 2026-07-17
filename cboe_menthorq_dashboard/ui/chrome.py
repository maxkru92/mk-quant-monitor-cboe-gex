"""
Reusable HTML chrome components
================================

Tiny raw-HTML helpers that mirror the TSX React design primitives (terminal
header, badges, market-clock strip). All output is ``unsafe_allow_html=True``
safe — no external assets, no user-controlled content is rendered as raw HTML.
"""

from __future__ import annotations


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
# Market clock — JS-injected, ticks locally in the user's browser
# ------------------------------------------------------------------ #
#   Four financial-center clocks. NYC/LON/FRA/TYO shown in the compact
#   strip; the full set is in the title=tooltip (hover). The script
#   self-renders every 1s — Streamlit does NOT rerun, so the CBOE API
#   is never spammed. Period-window logic mirrors market-clock.tsx.
# ------------------------------------------------------------------ #
_MARKET_CLOCK_HTML = """
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
  <div id="vc-clock-row" style="
    display:flex;gap:16px;font-family:'JetBrains Mono',monospace;
    font-size:0.65rem;letter-spacing:0.1em;"></div>
</div>
<script>
(function () {
  const markets = [
    { code: 'NYC', tz: 'America/New_York', openStart: 14*60+30, openEnd: 21*60   },
    { code: 'LON', tz: 'Europe/London',    openStart: 8*60,     openEnd: 16*60+30 },
    { code: 'FRA', tz: 'Europe/Berlin',    openStart: 8*60,     openEnd: 16*60+30 },
    { code: 'TYO', tz: 'Asia/Tokyo',       openStart: 0,        openEnd: 6*60    }
  ];
  // compact header cities (matches market-clock.tsx COMPACT_MARKETS const)
  const compact = ['NYC', 'LON'];

  function fmt(d, tz) {
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false
    }).format(d);
  }
  function isOpen(d, m) {
    const day = d.getUTCDay();
    if (day === 0 || day === 6) return false;
    const mins = d.getUTCHours() * 60 + d.getUTCMinutes();
    return mins >= m.openStart && mins < m.openEnd;
  }

  function render() {
    const now = new Date();
    const row = document.getElementById('vc-clock-row');
    if (!row) return;

    // Tooltip string built once — full 4-city dump w/ open/closed state.
    const tip = markets.map(m =>
      m.code + ' ' + fmt(now, m.tz) + ' ' + (isOpen(now, m) ? '● OPEN' : '○ closed')
    ).join('\\n');

    row.innerHTML = '';
    markets.filter(m => compact.includes(m.code)).forEach(m => {
      const open  = isOpen(now, m);
      const color = open ? '#00e676' : 'rgba(255,255,255,0.40)';
      const dotBg = open ? '#00e676' : 'rgba(255,255,255,0.30)';
      const pulse = open ? 'animation:vcPulse 1s infinite;' : '';

      const div = document.createElement('div');
      div.style.cssText = 'display:inline-flex;align-items:center;gap:5px;color:' + color + ';';
      div.title = tip;
      div.innerHTML =
        '<span style="width:6px;height:6px;border-radius:50%;background:' + dotBg + ';' + pulse + '"></span>' +
        '<span style="font-size:0.50rem;font-weight:700;letter-spacing:0.10em;">' + m.code + '</span>' +
        '<span style="font-family:JetBrains Mono,monospace;font-size:0.70rem;font-variant-numeric:tabular-nums;">' + fmt(now, m.tz) + '</span>';
      row.appendChild(div);
    });
  }

  render();
  // Tick every second. The SetInterval runs entirely in the browser — no
  // Streamlit rerun, no CBOE API call, no Python state churn.
  setInterval(render, 1000);
})();
</script>
"""


def render_market_clock() -> str:
    """Return HTML/JS for the live market clock strip. Inject via
    ``st.components.v1.html(MARKET_CLOCK_HTML, height=44)``."""
    return _MARKET_CLOCK_HTML

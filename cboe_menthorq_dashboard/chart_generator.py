"""
Krupp Capital — Dark Institutional GEX Chart Generator (v3)
============================================================

PURE RENDERING MODULE (2026-07 architecture review, Candidate 1)

Deleted (duplicated from data_fetcher / gex_calculator / greeks):
  - fetch_cboe_chain()          → was a CBOE fetcher (duplicate of LiveOptionsFetcher)
  - bsm_gamma / bsm_gamma_from_iv → was BSM gamma (duplicate of greeks.black_scholes_greeks)
  - parse_opra / aggregate_strikes → was CBOE JSON parser (duplicate of data_fetcher)
  - compute_levels              → was level computer (duplicate of gex_calculator.levels)
  - StrikeRow dataclass         → replaced by pd.DataFrame from gex_by_strike()
  - render_chart_bytes()        → replaced by three-line call pattern
  - CLI / main()                → not needed in dashboard; use the modules directly

New single entry point:
  render_chart(symbol, by_strike, spot, ...) → PNG bytes

Palette imported from ui.theme (single source of truth).
Levels (callWall, putSupport) computed inline from by_strike DataFrame.

Sole author of all visual output: Volatility Vince — leitender Quantitative
Analyst & Senior Options Market Maker, Krupp Capital Quant Research Desk.

Color palette, layout, watermark, and cover-page hierarchy are bound to the
authoritative styleguide at /Users/maximiliankrupp/Documents/VolatilityVince/docs/REPORT_STYLEGUIDE.md
and the persona at /Users/maximiliankrupp/Documents/VolatilityVince/docs/SOUL.md.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from cboe_menthorq_dashboard.ui.theme import (
    MAIN_BG,
    PANEL_BG,
    GRID,
    ZERO_LINE,
    TEXT_PRI,
    TEXT_SEC,
    CYAN,
    EMERALD as GEX_POS,
    ROSE as GEX_NEG,
    AMBER as SPOT_LINE,
    LIGHT_GREEN,
    PINK_RED,
)


# ================================================================
# WATERMARK (binding to spec §4 — bottom-right, M-Gray, lowercase)
# ================================================================
HASHTAG_TEXT  = "crafted by Krupp Capital"
HASHTAG_COLOR = ZERO_LINE
HASHTAG_OPACITY = 0.7
HASHTAG_FONTSIZE = 7
HASHTAG_FAMILY = "monospace"


# ================================================================
# RANGE / TICK FORMATTING (edge cases for SPX vs USO scale)
# ================================================================
def _strike_xlim(by_strike: pd.DataFrame, spot: float) -> tuple[float, float]:
    """Trim x-axis to ~5th–95th percentile of total-OI strikes (drops deep-OTM noise).

    ``by_strike`` is the output of ``gex_calculator.gex_by_strike()``.
    """
    if by_strike.empty or "total_oi" not in by_strike.columns:
        return spot * 0.8, spot * 1.2

    # Sort descending by total_oi so we can find weight-based 5/95 percentiles
    sorted_ = by_strike.sort_values("total_oi", ascending=False)
    total = sorted_["total_oi"].sum()
    if total <= 0 or len(sorted_) < 5:
        lo, hi = by_strike.index.min(), by_strike.index.max()
        pad = max((hi - lo) * 0.05, 1.0)
        return max(0, lo - pad), hi + pad

    cum = 0.0
    lo, hi = sorted_.index[-1], sorted_.index[0]  # fallback
    for strike_val, oi in zip(sorted_.index, sorted_["total_oi"]):
        cum += oi
        if cum >= total * 0.05:
            lo = strike_val
            break
    cum = 0.0
    for strike_val, oi in zip(sorted_.index, sorted_["total_oi"]):
        cum += oi
        if cum >= total * 0.95:
            hi = strike_val
            break

    pad = max((hi - lo) * 0.04, 1.0)
    return lo - pad, hi + pad


def _strike_tick_formatter(spot: float):
    """K-notation for indices with large strikes (SPX, NDX), dollar for others."""
    if spot >= 1000:
        def fmt(x, _pos=None):
            return f"{x / 1000:.1f}K"
        return fmt

    def fmt(x, _pos=None):
        return f"{x:,.0f}"
    return fmt


# ================================================================
# COVER-PAGE STRIP (binding to spec §2 hierarchy)
# ================================================================
def _draw_cover_strip(fig, theme_text: str, key_data: dict):
    """Hierarchy per spec:
      1. KRUPP CAPITAL                       (primary text, max prominence)
      2. [Thema]                             (cyan, dominant)
      3. Key-Data Grid                       (labels in TEXT_SEC, values in TEXT_PRI)
    """
    # Subtle cyan accent rule above the title
    fig.add_artist(plt.Line2D([0.04, 0.13], [0.965, 0.965], color=CYAN, linewidth=2.0,
                              transform=fig.transFigure, alpha=0.9, zorder=10))

    fig.text(0.05, 0.945, "KRUPP CAPITAL",
             color=TEXT_PRI, fontsize=19, fontweight="bold", family="monospace",
             ha="left", va="top")

    fig.text(0.05, 0.913, theme_text,
             color=CYAN, fontsize=12, fontweight="bold", family="monospace",
             ha="left", va="top")

    # Key-Data grid: 4 pairs, individually positioned for colour control
    parts = [
        ("DATE",        key_data.get("date", "—")),
        ("ANALYST",     key_data.get("analyst", "Volatility Vince")),
        ("INSTITUTION", key_data.get("institution", "Krupp Capital")),
        ("BEREICH",     key_data.get("bereich", "Quant Research Desk")),
    ]
    x_cursor = 0.05
    y_line = 0.888
    for i, (label, val) in enumerate(parts):
        prefix = "▸ " if i == 0 else " · "
        fig.text(x_cursor, y_line, prefix,
                 color=TEXT_SEC, fontsize=8, family="monospace", ha="left", va="top")
        x_cursor += 0.013 + 0.006 * len(prefix)
        fig.text(x_cursor, y_line, f"{label} ",
                 color=TEXT_SEC, fontsize=8, family="monospace", ha="left", va="top")
        x_cursor += 0.013 + 0.0085 * len(label) + 0.005
        fig.text(x_cursor, y_line, str(val),
                 color=TEXT_PRI, fontsize=8, family="monospace", ha="left", va="top")
        x_cursor += 0.013 + 0.0085 * len(str(val)) + 0.008


# ================================================================
# WATERMARK (binding to spec §4)
# ================================================================
def _draw_watermark(fig):
    fig.text(0.985, 0.024, HASHTAG_TEXT,
             color=HASHTAG_COLOR, fontsize=HASHTAG_FONTSIZE,
             family=HASHTAG_FAMILY, fontweight="normal",
             ha="right", va="bottom", alpha=HASHTAG_OPACITY, zorder=20)


# ================================================================
# RENDER — single entry point, accepts already-processed DataFrame
# ================================================================
def render_chart(
    symbol: str,
    by_strike: pd.DataFrame,
    spot: float,
    theme: Optional[str] = None,
    output_path: Optional[str] = None,
    date_label: Optional[str] = None,
) -> bytes:
    """Render the v3 dark-institutional GEX chart.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. "SPX", "SPY").
    by_strike : pd.DataFrame
        Output of ``gex_calculator.GEXCalculator.gex_by_strike()``.
        Must have columns: call_gex, put_gex, net_gex, call_oi, put_oi, total_oi.
        Index = strikes.
    spot : float
        Current spot price.
    theme : str, optional
        Cover-strip theme line (e.g. "GAMMA EXPOSURE PROFILE — DARK INSTITUTIONAL").
    output_path : str, optional
        If provided, also write the PNG bytes to this path.
    date_label : str, optional
        Date string for the cover strip (defaults to UTC now).

    Returns
    -------
    bytes
        PNG image bytes, safe for st.image() or Telegram sendPhoto.
    """
    if by_strike.empty:
        raise ValueError(f"No strike data for {symbol}; cannot render chart.")

    strikes = by_strike.index.values.astype(float)
    net_gex = by_strike["net_gex"].values
    call_gex = by_strike["call_gex"].values
    put_gex = by_strike["put_gex"].values
    call_oi = by_strike["call_oi"].values
    put_oi = by_strike["put_oi"].values

    # Compute levels inline from by_strike DataFrame
    above = by_strike[by_strike.index > spot]
    below = by_strike[by_strike.index < spot]
    call_wall = float(above["call_gex"].idxmax()) if not above.empty else None
    put_support = float(below["net_gex"].idxmin()) if not below.empty else None

    # Figure — sizing safe for Telegram (<1280 px)
    fig = plt.figure(figsize=(9.0, 6.8), facecolor=MAIN_BG)

    # ---- Cover-page strip ----
    if theme is None:
        theme = "GAMMA EXPOSURE PROFILE — DARK INSTITUTIONAL"
    if date_label is None:
        date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    key_data = {
        "date":        date_label,
        "analyst":     "Volatility Vince",
        "institution": "Krupp Capital",
        "bereich":     "Quant Research Desk",
    }
    _draw_cover_strip(fig, theme, key_data)

    # ---- PANEL 1: Call GEX (cyan) ----
    ax1 = fig.add_subplot(311, facecolor=PANEL_BG)
    ax1.bar(strikes, call_gex / 1e9, color=CYAN, alpha=0.85, width=1.0, zorder=2)
    ax1.axvline(spot, color=SPOT_LINE, linewidth=2.5, linestyle="--",
                label=f"Spot {spot:,.2f}", zorder=5)
    if call_wall is not None:
        ax1.axvline(call_wall, color=GEX_POS, linewidth=0.9, linestyle=":",
                    alpha=0.7, label=f"CW {call_wall:,.0f}", zorder=4)
    if put_support is not None:
        ax1.axvline(put_support, color=GEX_NEG, linewidth=0.9, linestyle=":",
                    alpha=0.7, label=f"PS {put_support:,.0f}", zorder=4)
    ax1.set_ylabel("Call GEX (B)", color=TEXT_SEC, fontsize=8, family="monospace")
    ax1.tick_params(colors=TEXT_SEC, labelsize=7)
    ax1.ticklabel_format(axis="y", useOffset=False)
    for sp in ax1.spines.values():
        sp.set_color(ZERO_LINE)
        sp.set_linewidth(0.6)
    ax1.grid(True, color=GRID, linewidth=0.3, alpha=0.5)
    ax1.xaxis.set_major_formatter(FuncFormatter(_strike_tick_formatter(spot)))
    ax1.set_xlim(_strike_xlim(by_strike, spot))
    ax1.legend(loc="upper right", fontsize=6, facecolor=PANEL_BG,
               edgecolor=ZERO_LINE, labelcolor=TEXT_SEC, framealpha=0.6)

    # ---- PANEL 2: Net GEX (green/red) ----
    ax2 = fig.add_subplot(312, facecolor=PANEL_BG, sharex=ax1)
    colors = [GEX_POS if v >= 0 else GEX_NEG for v in net_gex]
    ax2.bar(strikes, net_gex / 1e9, color=colors, alpha=0.9, width=1.0, zorder=2)
    ax2.axhline(0, color=ZERO_LINE, linewidth=0.9, zorder=5)
    ax2.axvline(spot, color=SPOT_LINE, linewidth=2.5, linestyle="--", alpha=0.85, zorder=4)
    ax2.set_ylabel("Net GEX (B)", color=TEXT_SEC, fontsize=8, family="monospace")
    ax2.tick_params(colors=TEXT_SEC, labelsize=7)
    ax2.ticklabel_format(axis="y", useOffset=False)
    for sp in ax2.spines.values():
        sp.set_color(ZERO_LINE)
        sp.set_linewidth(0.6)
    ax2.grid(True, color=GRID, linewidth=0.3, alpha=0.5)
    ax2.xaxis.set_major_formatter(FuncFormatter(_strike_tick_formatter(spot)))

    # ---- PANEL 3: OI (light-green calls / pink-red puts) ----
    ax3 = fig.add_subplot(313, facecolor=PANEL_BG, sharex=ax1)
    ax3.bar(strikes, call_oi / 1e3, color=LIGHT_GREEN, alpha=0.75, width=1.0,
            label="Call OI (k)", zorder=2)
    ax3.bar(strikes, -put_oi / 1e3, color=PINK_RED, alpha=0.75, width=1.0,
            label="Put OI (k)", zorder=2)
    ax3.axhline(0, color=ZERO_LINE, linewidth=0.9, zorder=5)
    ax3.axvline(spot, color=SPOT_LINE, linewidth=2.5, linestyle="--", alpha=0.85, zorder=4)
    ax3.set_ylabel("Open Interest (k contracts)", color=TEXT_SEC, fontsize=8, family="monospace")
    ax3.set_xlabel(f"Strike ({'K-notation' if spot >= 1000 else 'USD'})",
                   color=TEXT_SEC, fontsize=8, family="monospace")
    ax3.tick_params(colors=TEXT_SEC, labelsize=7)
    for sp in ax3.spines.values():
        sp.set_color(ZERO_LINE)
        sp.set_linewidth(0.6)
    ax3.grid(True, color=GRID, linewidth=0.3, alpha=0.5)
    ax3.xaxis.set_major_formatter(FuncFormatter(_strike_tick_formatter(spot)))
    ax3.legend(loc="upper right", fontsize=6, facecolor=PANEL_BG,
               edgecolor=ZERO_LINE, labelcolor=TEXT_SEC, framealpha=0.6)

    # Reserve top 18% for cover strip; bottom 6% for watermark.
    fig.subplots_adjust(top=0.78, bottom=0.07, left=0.06, right=0.97, hspace=0.30)

    # ---- Watermark (binding to spec §4) ----
    _draw_watermark(fig)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=125, facecolor=MAIN_BG, edgecolor="none",
                bbox_inches="tight", pad_inches=0.10)
    plt.close(fig)

    png_bytes = _enforce_telegram_dimensions(buf.getvalue(), max_width=1280)
    if output_path:
        with open(output_path, "wb") as f:
            f.write(png_bytes)
    return png_bytes


# ================================================================
# TELEGRAM SIZE GUARD (binding to sendPhoto 1280 px width cap)
# ================================================================
TELEGRAM_MAX_WIDTH_PX = 1280


def _enforce_telegram_dimensions(png_bytes: bytes, max_width: int = TELEGRAM_MAX_WIDTH_PX) -> bytes:
    """Guarantee the PNG fits Telegram's sendPhoto width constraint (1280 px)."""
    try:
        from PIL import Image
    except ImportError:
        import warnings
        warnings.warn(
            "Pillow is required for Telegram ≤1280 px compliance. "
            "Add `Pillow>=9.1.0` to requirements.txt.",
            RuntimeWarning,
            stacklevel=2,
        )
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    if img.width <= max_width:
        return png_bytes
    new_w = max_width
    new_h = int(round(img.height * (new_w / img.width)))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    if img.width > max_width:
        raise ValueError(
            f"LANCZOS resize arithmetic exceeded max_width: {img.width} > {max_width}"
        )
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()

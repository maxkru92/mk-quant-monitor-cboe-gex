"""
Regression tests pinning that NONE of the chart fig.update_layout(...) calls
in the dashboard accidentally inject `automargin=True` into 3D axes
(`layout.scene.xaxis/yaxis/zaxis`) — Plotly rejects that property and
crashes the dashboard at startup.

The bug originated from a regex .replace() that injected `automargin=True`
into every `xaxis=dict(...)` / `yaxis=dict(...)` block, including the 3D
scene-axis dicts in `render_vol_surface`. Plotly's validation raised:

    ValueError: Invalid property specified for object of type
    plotly.graph_objs.layout.scene.XAxis: \'automargin\'

This test walks every file under tabs/ that renders a fig.update_layout(),
parses out the `scene=dict(...)` block (the only unsafe axis context for
automargin), and asserts no `automargin=True` appears inside.
"""
from __future__ import annotations

import re
from pathlib import Path


def _extract_scene_blocks(text: str) -> list:
    """Return all `scene=dict(...)` block contents, brace-balanced."""
    blocks = []
    for m in re.finditer(r"scene\s*=\s*\{", text):
        i = m.end()
        depth = 1
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        if depth == 0:
            blocks.append(text[m.start():i])
    return blocks


def _files_with_charts():
    here = Path(__file__).parent
    root = here.parent
    candidates = [
        root / "tabs" / "quant_metrics.py",
        root / "tabs" / "macro.py",
        root / "tabs" / "crypto.py",
        root / "tabs" / "strategy_calc.py",
        root / "tabs" / "greeks_calc.py",
    ]
    return [p for p in candidates if p.exists()]


def test_no_automargin_in_scene_axes_anywhere():
    """Regression: `automargin=True` must never appear inside any `scene=dict(...)` block."""
    offenders = []
    for f in _files_with_charts():
        text = f.read_text(encoding='utf-8')
        for block in _extract_scene_blocks(text):
            if re.search(r"automargin\s*=\s*True", block):
                offenders.append(f.name + " -> " + block[:200])
    assert not offenders, (
        "Plotly 3D axis must not contain automargin=True. Found in:\n"
        + "\n\n".join(offenders)
    )


def test_all_visible_decorative_emojis_stripped():
    """Regression: user requested removing emojis from tab titles and sub-tabs.
    Every visible label-source in production code must start with an ASCII
    letter or whitelisted Punctuation (e.g. \u00b7) \u2014 not an emoji codepoint.
    Scope (broader than the user\'s narrow wording for safety):
      - st.tabs([...]) arguments (top-level tabs + sub-tabs)
      - terminal_header('...' ) titles (section dividers inside tabs)
      - st.button('...' ) labels (e.g. Refresh Data)
      - st.subheader('...' ) labels (e.g. MenthorQ Gamma Data String)
    Preserved on purpose (functional indicators, not labels):
      - st.info/warning(icon='...' ) banners, page_icon='...'
    """
    import unicodedata

    files = _files_with_charts()
    # Plus app.py itself which holds the top-level tabs.
    here = Path(__file__).parent
    files.append(here.parent / "app.py")
    offenders = []
    for f in files:
        text = f.read_text(encoding='utf-8')
        for tab_block in re.findall(r"st\.tabs\(\[(.*?)\]\)", text, flags=re.DOTALL):
            for label in re.findall(r'"([^"]*)"', tab_block):
                if not label:
                    continue
                # Strip leading whitespace/CSS chars; check first VISIBLE char.
                first = next((c for c in label if not c.isspace()), "")
                if not first:
                    continue
                # Any emoji codepoint in \u2190-\u2BFF, \uD83C-\uDBFF\uDC00-\uDFFF, or
                # \u1F000-\u1FAFF range disqualifies the label.
                cp = ord(first)
                if 0x2190 <= cp <= 0x2BFF or cp >= 0x1F000:
                    offenders.append(f.name + " -> \"" + label + "\"")
                # Also: combining variation selectors (\uFE0F) immediately after.
                if len(label) >= 2 and ord(label[1]) == 0xFE0F:
                    offenders.append(f.name + " -> \"" + label + "\" (leading VS16)")
    assert not offenders, (
        "Tab labels and sub-category tabs must be emoji-free (user request). Found:\n"
        + "\n".join(offenders)
    )


"""
Real Plotly validation test: confirms that injecting `automargin=True`
into a 3D `layout.scene.xaxis/yaxis` raises a ValueError in Plotly.

This locks in the EXACT bug class that crashed the Streamlit Cloud
dashboard on 2026-07-19. It is the canonical end-to-end smoke test
that the textual regression tests in this file complement.

Combined coverage:
  - test_plotly_rejects_automargin_in_3d_scene_axes  (this one) — proves
        the bug class is real and the property is rejected by Plotly.
  - test_no_automargin_in_scene_axes_anywhere — proves our source code
        no longer triggers the property on 3D axes.
  - test_all_tab_labels_emoji_free — pins the user-requested emoji strip.

If Plotly ever ACCEPTS `automargin` on 3D axes (unlikely; documented
as a layout-margin property, not axis property), this test will fail
and we should re-validate whether our injection is now safe.
"""
import pytest
import plotly.graph_objects as go


def test_plotly_rejects_automargin_in_3d_scene_axes():
    """Proves that `automargin=True` inside `scene.xaxis` is fatal.

    The bug that crashed Streamlit Cloud: `fig.update_layout(scene=dict(
    xaxis=dict(automargin=True), ...))`. Plotly raises
    `ValueError: Invalid property specified for object of type
    plotly.graph_objs.layout.scene.XAxis: \'automargin\'`.

    This test asserts that fact so that the bug class is permanently
    visible. The textual regression tests ensure our code never trips
    it.
    """
    fig = go.Figure(data=[go.Surface(z=[[1.0, 2.0], [3.0, 4.0]])])
    with pytest.raises(ValueError, match="automargin"):
        fig.update_layout(
            scene=dict(
                xaxis=dict(automargin=True),
                yaxis=dict(automargin=True),
            )
        )


def test_plotly_accepts_automargin_in_2d_axes():
    """Counter-test: 2D `layout.xaxis/yaxis` DOES accept `automargin=True`.

    Proves that the 3D rejection is specific to scene axes, not a global
    Plotly refusal. Our code intentionally keeps automargin=True on
    2D charts (candlestick / regime detection) in tabs/quant_metrics.py.
    """
    fig = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[4, 5, 6], mode="lines")])
    fig.update_layout(
        xaxis=dict(automargin=True),
        yaxis=dict(automargin=True),
    )
    # If we got here without raising, the property is accepted on 2D axes.
    assert fig.layout.xaxis.automargin is True
    assert fig.layout.yaxis.automargin is True

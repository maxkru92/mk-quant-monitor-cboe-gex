"""Regression tests for ``strategy_calc._compute_slider_step``.

Streamlit 1.59 raises ``StreamlitAPIException`` when ``min_value``,
``max_value`` and ``step`` don't share the same numeric type.
``round(initial_spot * 0.001)`` returns ``int`` — for SPX @ $7,457.69
that's ``7`` (int), which made the spot-price slider on the
Strategy + Monte Carlo tab crash on first render with::

    StreamlitAPIException: ... min_value=1.0, max_value=20000.0,
    step=7 — types must match

The fix forces ``float(...)`` on the result. These tests are the
lock-in for that fix; remove the ``float()`` cast and they'll fail.

.. note::

   **AppTest parity gap (streamlit version split).** The local
   ``AppTest`` slider validator in streamlit **1.40.0** does NOT
   enforce the type match the way production streamlit **1.59.2**
   does. The integration test ``test_strategy_calc_render_with_high_spot``
   in ``test_app_smoke.py`` passes at spot=5945.0 with ``step=6``
   (int) because AppTest is more permissive. Production crashes were
   the only signal — these unit tests close that gap.
"""

from __future__ import annotations

import pytest

from cboe_menthorq_dashboard.tabs.strategy_calc import _compute_slider_step


# ------------------------------------------------------------------ #
# Core invariant — type is always float
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("initial_spot,expected_step", [
    pytest.param(1.0,     1.0, id="floor_min"),
    pytest.param(50.0,    1.0, id="floor_50"),
    pytest.param(100.0,   1.0, id="floor_100"),
    pytest.param(190.0,   1.0, id="aapl_scale"),         # round(0.19) = 0 → max(1,0) = 1
    pytest.param(600.0,   1.0, id="spy_scale_600"),      # round(0.6)  = 1
    pytest.param(1000.0,  1.0, id="boundary_1000"),      # round(1.0)  = 1
    pytest.param(5945.0,  6.0, id="mid_cap_5945"),       # round(5.945)= 6
    pytest.param(7457.69, 7.0, id="spx_realistic_bug"),  # round(7.457) = 7 (the actual bug)
    pytest.param(20000.0, 20.0, id="ceil_clamp_20k"),
])
def test_compute_slider_step_returns_float_with_expected_value(initial_spot, expected_step):
    """Type is float (Streamlit 1.59 requirement) AND numeric value matches."""
    result = _compute_slider_step(initial_spot)
    assert isinstance(result, float), (
        f"_compute_slider_step({initial_spot!r}) returned "
        f"{type(result).__name__}, expected float — Streamlit 1.59 "
        f"rejects non-matching types."
    )
    assert result == pytest.approx(expected_step)


# ------------------------------------------------------------------ #
# Edge cases — defensive against bad input + locks in the upstream clamp.
# inf is contract-tested below (raises OverflowError) — the helper does
# NOT gracefully handle inf; that responsibility belongs to
# ``render_strategy_calculator``'s upstream clamp:
#   initial_spot = max(1.0, min(float(spot_default), 20000.0))
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("bad,case_id", [
    pytest.param(0.0,    "zero"),
    pytest.param(-100.0, "neg_100"),
    pytest.param(-1.0,   "neg_1"),
])
def test_compute_slider_step_clamps_non_positive_to_one(bad, case_id):
    """0 / negative spot → clamped to 1.0 (defensive against bad input).

    Locks in that ``render_strategy_calculator``'s upstream clamp
    (``max(1.0, min(spot, 20000.0))``) + this helper's ``max(1.0, ...)``
    floor work as a defense-in-depth combo in case upstream isn't called
    (e.g. future callers skip the wrapper).
    """
    result = _compute_slider_step(bad)
    assert isinstance(result, float)
    assert result == pytest.approx(1.0)


def test_compute_slider_step_in_inf_does_not_silently_succeed():
    """Contract: ``_compute_slider_step`` does NOT silently swallow ``inf``.

    ``round(float("inf"))`` raises :class:`OverflowError`. Locking that in
    via ``pytest.raises`` prevents a future refactor from accidentally
    turning inf into a finite-but-wrong step value (e.g. via ``min`` or
    ``if math.isfinite`` guards) without a deliberate test signal.

    The upstream clamp in ``render_strategy_calculator`` is the layer
    that *prevents* the helper from ever seeing ``inf``; the helper
    itself stays strict.
    """
    with pytest.raises(OverflowError, match=r"(?i)infinity"):
        _compute_slider_step(float("inf"))


# ------------------------------------------------------------------ #
# Single canonical reproducer — the actual bug the live deploy hit.
# ------------------------------------------------------------------ #
def test_compute_slider_step_spx_realistic_spot_is_float():
    """SPX @ $7,457.69 → step = 7.0 (int promotion caught by float()).

    Without the ``float(...)`` cast, this returns ``7`` (int) and
    Streamlit 1.59 raises ``StreamlitAPIException`` on the
    Strategy + Monte Carlo tab.
    """
    result = _compute_slider_step(7457.69)
    assert isinstance(result, float)   # the real invariant
    assert result == pytest.approx(7.0)

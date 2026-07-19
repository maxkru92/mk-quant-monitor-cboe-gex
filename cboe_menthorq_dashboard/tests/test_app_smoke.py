"""
Smoke tests via ``streamlit.testing.v1.AppTest``.

.. note::

   ``AppTest.from_function()`` has a known gotcha: it extracts the
   function's source via ``inspect.getsourcelines()`` and re-executes
   it in an isolated synthetic module. Module-level imports in the
   original module (e.g. ``import streamlit as st`` at the top of
   ``strategy_calc.py``) are **not** visible inside the re-executed
   scope, so the function body fails with ``NameError`` for ``st`` or
   for any name defined at module level (``render_strategy_calculator``,
   ``_ensure_session_state``, etc.).

   These tests therefore use ``AppTest.from_string()`` with
   **self-contained Python scripts** that re-import ``streamlit`` and
   the tab module inside the script body. Values are baked into the
   f-string with no closures — primitives only.

Mounts ``strategy_calc.render()`` and ``greeks_calc.render()`` in a
simulated script run. Asserts no exception on cold start, that init
blocks populate ``session_state``, and that rendered canvas collects
the expected UI elements (5 sliders, 8 leg number_inputs, 2 toggle
buttons).

Streamlit AppTest requires ``streamlit>=1.28``. Local version is
1.40.0; build logs report 1.59.2 in Streamlit Cloud.

Run with::

    python -m pytest cboe_menthorq_dashboard/tests/test_app_smoke.py -v
"""
from __future__ import annotations

import pytest

# Version gate: streamlit 1.43+. Two reasons needed together:
#   - AppTest.from_string / .exception / .session_state exist since 1.28.
#   - The dashboard's render() uses `st.button(width='stretch')` and the
#     `width` kwarg was added to button() in 1.43 (along with the
#     use_container_width → 'stretch' deprecation timeline).
# Production runs streamlit 1.59.2 in Streamlit Cloud; local/CI must match.
pytest.importorskip("streamlit", minversion="1.43")


# ------------------------------------------------------------------ #
# Synthetic-script builders (self-contained — primitives baked in)
# ------------------------------------------------------------------ #
def _strategy_script(spot_default: float) -> str:
    """Self-contained script for ``strategy_calc.render()`` with yfinance
    stubbed. Re-imports streamlit + the tab module + unittest.mock *inside*
    the script body so the AppTest isolated scope sees them.
    """
    return f"""
import streamlit as st
from unittest.mock import patch
from cboe_menthorq_dashboard.tabs import strategy_calc

_fake_mc = {{"mu": 0.08, "sigma": 0.25, "source": "fallback-fixed"}}
with patch.object(strategy_calc, "get_mc_params", return_value=_fake_mc):
    strategy_calc.render({float(spot_default)})
"""


def _greeks_script(spot_default: float, chain=None) -> str:
    """Self-contained script for ``greeks_calc.render()`` with ``chain=None``
    by default (the CBOE ATM-IV fallback path, no network).
    """
    chain_literal = "None" if chain is None else repr(chain)
    return f"""
import streamlit as st
from cboe_menthorq_dashboard.tabs import greeks_calc
greeks_calc.render({float(spot_default)}, {chain_literal})
"""


def _strategy_at(spot_default: float):
    """Build an AppTest instance around the strategy_calc script."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(_strategy_script(spot_default))
    at.run()
    return at


def _greeks_at(spot_default: float, chain=None):
    """Build an AppTest instance around the greeks_calc script."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(_greeks_script(spot_default, chain))
    at.run()
    return at


# ------------------------------------------------------------------ #
# strategy_calc integration smoke
# ------------------------------------------------------------------ #
def test_strategy_calc_render_first_run_no_exception():
    """strategy_calc.render() completes without exception on a cold start."""
    at = _strategy_at(spot_default=100.0)
    assert not at.exception, f"strategy_calc.render() raised: {at.exception}"


def test_strategy_calc_render_no_session_state_keyerror():
    """Hard guard: NOT a SessionStateKeyError or any session-state KeyError.

    The previous audit verified that every read goes through
    ``safe_get()`` — this test fails fast if a future refactor regresses
    that invariant.
    """
    at = _strategy_at(spot_default=100.0)
    msg = str(at.exception or "")
    assert "SessionStateKeyError" not in msg, f"SessionStateKeyError: {msg}"
    assert "KeyError" not in msg, f"KeyError on session_state: {msg}"


def test_strategy_calc_render_with_high_spot():
    """SPX-scale spot (5945.0) — realistic smoke at $6K notional."""
    at = _strategy_at(spot_default=5945.0)
    assert not at.exception, f"raised: {at.exception}"


def test_strategy_calc_render_iron_condor_has_8_leg_inputs():
    """iron_condor default has 4 legs × 2 fields (strike + premium) = 8 number_inputs.

    Locks in both that the iron_condor default still has 4 legs AND
    that every leg input was rendered (no early return).
    """
    at = _strategy_at(spot_default=100.0)
    assert not at.exception
    assert len(at.number_input) == 8, (
        f"Expected 8 leg number_inputs (iron_condor: 4 legs x 2 fields), "
        f"got {len(at.number_input)}"
    )


def test_strategy_calc_render_session_state_initialized():
    """After first run, session_state holds the canonical defaults set by
    ``_ensure_session_state`` (strat_*) and the Monte Carlo init block
    (mc_*). This proves the init blocks fire before any read.
    """
    at = _strategy_at(spot_default=100.0)
    assert not at.exception
    ss = at.session_state
    # _ensure_session_state writes these on first run:
    assert ss["strat_key"] == "iron_condor"
    assert ss["strat_spot"] == pytest.approx(100.0)
    assert isinstance(ss["strat_legs"], list) and len(ss["strat_legs"]) == 4
    # Monte Carlo init block writes these:
    assert ss["mc_n_paths"] == 10000
    assert ss["mc_horizon"] == 20
    assert ss["mc_seed"] == 42


# ------------------------------------------------------------------ #
# greeks_calc integration smoke
# ------------------------------------------------------------------ #
def test_greeks_calc_render_first_run_no_exception():
    """greeks_calc.render() (chain=None) completes without exception."""
    at = _greeks_at(spot_default=100.0)
    assert not at.exception, f"greeks_calc.render() raised: {at.exception}"


def test_greeks_calc_render_no_session_state_keyerror():
    """Hard guard: NOT a SessionStateKeyError or any session-state KeyError."""
    at = _greeks_at(spot_default=100.0)
    msg = str(at.exception or "")
    assert "SessionStateKeyError" not in msg, f"SessionStateKeyError: {msg}"
    assert "KeyError" not in msg, f"KeyError on session_state: {msg}"


def test_greeks_calc_render_has_5_sliders():
    """5 greek input sliders (S, K, T, r, sigma) must be present."""
    at = _greeks_at(spot_default=100.0)
    assert not at.exception
    assert len(at.slider) == 5, (
        f"Expected 5 sliders, got {len(at.slider)}"
    )


def test_greeks_calc_render_has_call_put_toggles():
    """At least 2 toggle buttons (CALL / PUT) must be present."""
    at = _greeks_at(spot_default=100.0)
    assert not at.exception
    assert len(at.button) >= 2, (
        f"Expected >= 2 buttons (CALL/PUT toggles), got {len(at.button)}"
    )


def test_greeks_calc_render_session_state_initialized():
    """After first run, session_state holds the gk_* defaults set by the
    ``_DEFAULTS_GK`` init loop in render()."""
    at = _greeks_at(spot_default=100.0)
    assert not at.exception
    ss = at.session_state
    # _DEFAULTS_GK init loop writes these on first run:
    assert ss["gk_S"] == pytest.approx(100.0)
    assert ss["gk_K"] == pytest.approx(100.0)            # initial_k = round(initial_spot)
    assert ss["gk_T"] == pytest.approx(0.25)
    assert ss["gk_r"] == pytest.approx(0.04)
    assert ss["gk_sigma"] == pytest.approx(0.30)         # fallback (chain=None)
    assert ss["gk_type"] == "call"

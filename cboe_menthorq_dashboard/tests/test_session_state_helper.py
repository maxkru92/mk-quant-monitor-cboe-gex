"""
Unit tests for cboe_menthorq_dashboard.ui.session_state.safe_get.

The helper's contract is: NEVER raise, even if the proxy is broken, the
key is missing, or the chain has weird types. These tests verify that
on the 5 realistic attack vectors.

Run with::

    python -m pytest cboe_menthorq_dashboard/tests/test_session_state_helper.py -v
"""
from __future__ import annotations

from types import SimpleNamespace


# ------------------------------------------------------------------ #
# Fake streamlit objects (no monkeypatching — helper takes st as arg)
# ------------------------------------------------------------------ #
class _ItemRaisingState:
    """Mock session_state whose __getitem__ raises a chosen exception."""

    def __init__(self, exc):
        self._exc = exc

    def __getitem__(self, key):
        raise self._exc(key)


class _Store:
    """Mock session_state that stores keys (happy path)."""

    def __init__(self, store):
        self._store = dict(store)

    def __getitem__(self, key):
        return self._store[key]


class _BrokenSt:
    """Mock streamlit whose ``.session_state`` attribute access raises."""

    def __getattr__(self, name):
        raise AttributeError(name)


# ------------------------------------------------------------------ #
# Contract tests
# ------------------------------------------------------------------ #
def test_safe_get_returns_stored_value_when_present():
    """Happy path: key exists → returns value (default ignored)."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    fake_st = SimpleNamespace(
        session_state=_Store({"foo": 42, "bar": "hello"}),
    )
    assert safe_get(fake_st, "foo") == 42
    assert safe_get(fake_st, "bar") == "hello"
    assert safe_get(fake_st, "foo", "ignored_default") == 42  # default unused


def test_safe_get_returns_default_on_keyerror():
    """KeyError (real Streamlit's behavior on missing key) → default."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    fake_st = SimpleNamespace(
        session_state=_ItemRaisingState(KeyError),
    )
    assert safe_get(fake_st, "anything", "default") == "default"
    assert safe_get(fake_st, "anything", 0) == 0
    assert safe_get(fake_st, "anything") is None  # default = None when omitted


def test_safe_get_returns_default_on_attributeerror_via_broken_proxy():
    """AttributeError when session_state attr itself is unreachable → default."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    # _BrokenSt raises AttributeError on every attribute access; in particular
    # ``fake_st.session_state`` will fail, which means ``st.session_state[key]``
    # raises AttributeError before __getitem__ is even reached.
    assert safe_get(_BrokenSt(), "anything", "fallback") == "fallback"


def test_safe_get_returns_default_on_typeerror():
    """TypeError on weird proxy return → default."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    fake_st = SimpleNamespace(
        session_state=_ItemRaisingState(TypeError),
    )
    assert safe_get(fake_st, "key", 99) == 99
    assert safe_get(fake_st, "key", None) is None


def test_safe_get_returns_default_when_session_state_is_none():
    """session_state attr is None → TypeError on None[key] → default."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    fake_st = SimpleNamespace(session_state=None)
    # None[key] raises TypeError, caught, default returned.
    assert safe_get(fake_st, "key", "fallback") == "fallback"


def test_safe_get_never_raises_across_all_attack_vectors():
    """Meta-test: across every realistic broken-proxy shape, no exception escapes."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get

    scenarios = [
        SimpleNamespace(session_state=_ItemRaisingState(KeyError)),
        SimpleNamespace(session_state=_ItemRaisingState(AttributeError)),
        SimpleNamespace(session_state=_ItemRaisingState(TypeError)),
        SimpleNamespace(session_state=None),
        _BrokenSt(),
    ]
    for i, fake_st in enumerate(scenarios):
        # If any scenario raises, the test fails here (we catch it but the
        # pytest assertion below would never run; loop iteration would die).
        result = safe_get(fake_st, "any_key", "fallback_default")
        assert result == "fallback_default", (
            f"scenario #{i} returned {result!r} instead of fallback"
        )


def test_safe_get_preserves_truthy_falsy_values():
    """Helper must NOT swallow legitimate stored values (no truthiness filtering)."""
    from cboe_menthorq_dashboard.ui.session_state import safe_get
    fake_st = SimpleNamespace(
        session_state=_Store({
            "zero": 0, "empty": "", "none": None, "false": False,
        }),
    )
    assert safe_get(fake_st, "zero") == 0
    assert safe_get(fake_st, "empty") == ""
    assert safe_get(fake_st, "none") is None
    assert safe_get(fake_st, "false") is False
    # Missing key still returns the explicit default.
    assert safe_get(fake_st, "missing", "default") == "default"

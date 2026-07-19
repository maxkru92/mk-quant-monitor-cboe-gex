"""
Session-state read helper.

Streamlit (1.59.x) raises ``KeyError`` (or ``AttributeError`` depending on
the proxy path) when a key is absent in ``st.session_state``:

1. Attribute access: ``st.session_state.foo`` triggers ``__getattr__``
   which raises KeyError when ``foo`` is missing.
2. Dict-style access: ``st.session_state["foo"]`` triggers
   ``__getitem__`` which raises the same.

Even with an init guard (``if "foo" not in st.session_state:``) ahead of
the read, future refactors may break the ordering or invalidate the
guard. ``safe_get`` is the single, audited pathway that exposes the
contract: "read only; never raise; default on miss."

Usage::

    from cboe_menthorq_dashboard.ui.session_state import safe_get

    # In a render() function:
    S    = safe_get(st, "gk_S",   initial_spot)   # default = render-local var
    legs = safe_get(st, "strat_legs", [])          # default = empty list
    gk   = safe_get(st, "gk_type", "call")         # default = string

The function intentionally takes ``st`` as the first argument so tests
can swap in hand-rolled mock streamlit objects without monkeypatching
globals.

What it does NOT do:

- It does not *write*. Writes remain the contract of explicit init
  blocks (e.g. ``_ensure_session_state`` in
  ``tabs/strategy_calc.py`` or the init loop in
  ``tabs/greeks_calc.py``). Once ``safe_get`` is established, the
  init blocks become the "first run" canonical initializer and
  ``safe_get`` is the runtime backstop.
- It does not validate the value's type. Callers store typed defaults.
- It does not log the miss. Silent fallback is the documented
  contract; add a different helper if logging is needed.
"""
from __future__ import annotations

from typing import Any


def safe_get(st, key: str, default: Any = None) -> Any:
    """Read ``st.session_state[key]`` and return ``default`` on any miss.

    Belt-and-suspenders for Streamlit 1.59.x's session-state access
    quirks. Never raises. Returns ``default`` if:

    - the key is missing (KeyError on ``__getitem__``),
    - the session-state proxy is unreachable (AttributeError),
    - the proxy returns a wrong-typed value (TypeError),
    - the chain holds ``None`` (TypeError on ``None[key]``).

    Args:
        st: a streamlit module or any object exposing ``.session_state``.
            Tests substitute a hand-rolled mock (e.g. ``SimpleNamespace``).
        key: the session-state key. Any hashable.
        default: returned if the key is missing or the read fails.

    Returns:
        The stored value under ``key``, or ``default`` if absent.
    """
    try:
        return st.session_state[key]
    except (KeyError, AttributeError, TypeError):
        return default

"""
Vol surface — CBOE chain binning to (K, T) IV meshgrid
=========================================================

Public API
----------
``get_vol_surface_mesh(chain, spot, n_strikes, n_expiries)``
    Build a ``(strikes_axis, times_axis, Z)`` IV grid from a live CBOE chain.
``get_vol_surface_source(chain)``
    ``\"cboe\"`` if the chain has enough IV data; ``\"demo\"`` otherwise.
``fallback_smile(K, T, S0)``
    Deterministic IV fallback matching the original ``_smile()`` formula.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def get_vol_surface_mesh(
    chain: Optional[pd.DataFrame],
    spot: float,
    n_strikes: int = 15,
    n_expiries: int = 12,
):
    """Build a (n_strikes × n_expiries) IV grid from the live CBOE chain.

    Returns ``(strikes_axis, times_axis, Z)`` where ``Z[i,j]`` is the
    implied-vol (in %) at strike[i] and expiry[j], or ``(None, None, None)``
    if insufficient chain data is available.

    Logic:
      1. Drop rows with iv ≤ 0 (CBOE returns 0 for strikes where the
         quote is too thin to compute IV).
      2. Pick the n_expiries closest-to-today (by timestamp distance).
      3. Strike axis = spot ± 20 % in n_strikes evenly-spaced steps.
      4. For each (strike, expiry) cell: nearest-by-strike lookup in the
         chain for that expiry, average call and put IVs (whichever exists).
    """
    if chain is None or chain.empty:
        return None, None, None
    if "iv" not in chain.columns or "strike" not in chain.columns or "type" not in chain.columns:
        return None, None, None

    df = chain.copy()
    df["_is_call"] = df["type"].astype(str).str.lower().str.startswith("c")

    # iv > 0 only
    df = df[df["iv"] > 0]
    if df.empty:
        return None, None, None

    now_ts = int(pd.Timestamp.utcnow().timestamp())
    if "expiration" in df.columns:
        df = df.copy()
        df["_exp_ts"] = pd.to_datetime(df["expiration"]).astype("int64") // 10**9
        df = df[df["_exp_ts"] >= now_ts]
        if df.empty:
            return None, None, None
        # n_expiries closest to today (only future ones)
        unique_exps = sorted(df["expiration"].drop_duplicates().tolist(),
                             key=lambda x: pd.to_datetime(x).timestamp() - now_ts)[:n_expiries]
        if not unique_exps:
            return None, None, None
        df = df[df["expiration"].isin(unique_exps)]
    else:
        return None, None, None

    # Strike axis
    strike_lo = max(0.01, float(spot) * 0.8)
    strike_hi = float(spot) * 1.2
    if strike_hi <= strike_lo:
        return None, None, None
    strikes_axis = np.linspace(strike_lo, strike_hi, n_strikes)

    # Times axis (years to expiry for each picked expiration)
    times_axis = np.array([
        max(1.0 / 365.0, (pd.to_datetime(e).timestamp() - now_ts) / (365.25 * 86400.0))
        for e in unique_exps
    ])

    # Mean call/put IV per (expiration, strike) first; reduce further pointwise.
    by_exp_strike = df.groupby(["expiration", "strike"])["iv"].mean().reset_index()

    Z = np.full((n_strikes, len(unique_exps)), np.nan, dtype=float)
    for j, exp in enumerate(unique_exps):
        sub = by_exp_strike[by_exp_strike["expiration"] == exp]
        if sub.empty:
            continue
        for i, k in enumerate(strikes_axis):
            idx = (sub["strike"] - k).abs().idxmin()
            Z[i, j] = float(sub.loc[idx, "iv"]) * 100.0  # render in %

    return strikes_axis, times_axis, Z


def get_vol_surface_source(chain: Optional[pd.DataFrame]) -> str:
    """``\"cboe\"`` if chain has enough IV data; ``\"demo\"`` if not."""
    if chain is None or chain.empty or "iv" not in chain.columns:
        return "demo"
    n_iv = int((chain["iv"] > 0).sum())
    return "cboe" if n_iv >= 20 else "demo"


def fallback_smile(K, T, S0=100.0):
    """Deterministic IV fallback matching the original ``_smile()`` formula."""
    logM = np.log(np.asarray(K) / S0)
    return 0.20 + 0.05 * logM ** 2 + 0.02 * logM + 0.03 * np.exp(-np.asarray(T) / 2.0)

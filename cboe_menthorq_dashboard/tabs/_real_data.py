"""
Real-data helpers for the Krupp Capital Quant Dashboard
========================================================

Single source of truth for pulling live data into the new Quant Metrics,
Strategy + Monte Carlo, and Greeks tabs. All chains come from
``data_fetcher.LiveOptionsFetcher`` upstream (cached for 5 min in
``app.py``); all daily price history comes from ``yfinance`` and is
cached here for 5 min via ``@st.cache_data``.

Every function returns a graceful fallback if the underlying data source
is unavailable (offline / 401 / timeout / empty chain). The caller should
inspect the source label (when one is provided) and switch the badge
between ``LIVE · CBOE`` / ``LIVE · YFINANCE`` and ``FALLBACK · DEMO``.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf


# ------------------------------------------------------------------ #
# 1. Vol surface — CBOE chain binning to (K, T) meshgrid
# ------------------------------------------------------------------ #
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
    """``"cboe"`` if chain has enough IV data; ``"demo"`` if not."""
    if chain is None or chain.empty or "iv" not in chain.columns:
        return "demo"
    n_iv = int((chain["iv"] > 0).sum())
    return "cboe" if n_iv >= 20 else "demo"


# ------------------------------------------------------------------ #
# 2. OHLC candles — yfinance ^GSPC, last N trading days
# ------------------------------------------------------------------ #
@st.cache_data(ttl=300, show_spinner=False)
def get_volatility_candles(symbol: str = "^GSPC", days: int = 30):
    """Return DataFrame(t, open, high, low, close, up) — last ``days`` rows.

    Falls back to a deterministic placeholder (matching the original
    volatility-chart.tsx algorithm) if yfinance errors out. Returns tuple
    ``(df, source)`` where source is ``"yfinance"`` or ``"demo"``.
    """
    df = _try_yf_history(symbol, days=days + 5, interval="1d")
    if df is None:
        return _placeholder_candles(days), "demo"
    df = df.tail(days).reset_index(drop=True).rename(columns={"Date": "t"})
    df = pd.DataFrame({
        "t": pd.to_datetime(df["t"]),
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low":  df["Low"].astype(float),
        "close": df["Close"].astype(float),
    })
    df["up"] = df["close"] >= df["open"]
    return df, "yfinance"


def _try_yf_history(_symbol: str, days: int = 90, interval: str = "1d"):
    try:
        hist = yf.Ticker(_symbol).history(period=f"{days}d", interval=interval)
        if hist is None or hist.empty:
            return None
        return hist.reset_index()
    except Exception:
        return None


def _placeholder_candles(days: int = 30) -> pd.DataFrame:
    """Deterministic fallback preset — matches volatility-chart.tsx algorithm."""
    base = 5800.0
    out = []
    now = pd.Timestamp.utcnow().normalize()
    for i in range(days):
        seed = (i * 9301 + 49297) % 233280
        r = seed / 233280.0
        amp = base * 0.012
        center = base + np.sin(i / 4) * amp + (r - 0.5) * amp * 1.6
        opn = center + (r - 0.5) * amp * 0.4
        cls = center + np.sin(i / 3 + 0.2) * amp * 0.9 + (r - 0.45) * amp * 0.5
        hi = max(opn, cls) + amp * 0.3 + r * amp * 0.4
        lo = min(opn, cls) - amp * 0.3 - r * amp * 0.4
        out.append({"t": now - pd.Timedelta(days=(days - i)),
                    "open": opn, "high": hi, "low": lo, "close": cls,
                    "up": cls >= opn})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ #
# 3. Regime detection — Cartesian 3-state (trend × vol) classifier
# ------------------------------------------------------------------ #
@st.cache_data(ttl=300, show_spinner=False)
def get_index_history(symbol: str = "^GSPC", days: int = 95):
    """Raw daily close history from yfinance. Returns DataFrame(t, close) or None.

    NOTE: ``symbol`` is intentionally NOT underscore-prefixed so Streamlit
    hashes it into the cache key — otherwise AAPL/BRK.A tabs would serve
    the cached ^GSPC bars from the first call.
    """
    hist = _try_yf_history(symbol, days=days, interval="1d")
    if hist is None:
        return None
    hist = hist.tail(days).reset_index(drop=True).rename(columns={"Date": "t"})
    return pd.DataFrame({"t": pd.to_datetime(hist["t"]),
                         "close": hist["Close"].astype(float)})


def _classify_regimes(closes: np.ndarray, returns: np.ndarray) -> dict:
    """Cartesian trend × vol classifier. Three macro states:

      0 = Bull / Low Vol  (20d return > +5% AND 20d vol ≤ p60-of-self)
      1 = Sideways        (everything else)
      2 = Bear / High Vol (20d return < −5% AND 20d vol > p60-of-self)
    """
    n = len(closes)
    macro = np.full(n, 1, dtype=int)  # default sideways
    if n < 25:
        return {"macros": macro.tolist(), "trans": [[0.7,0.2,0.1],[0.2,0.6,0.2],[0.1,0.2,0.7]],
                "probs": [("Bull",0.5,0),("Sideways",0.3,1),("Bear",0.2,2)]}

    # Compute 20d rolling realised vol (annualised) for the full window
    rets_series = pd.Series(returns)
    rolling_20 = rets_series.rolling(window=20, min_periods=5).std() * np.sqrt(252.0)
    rolling_20 = rolling_20.values  # NaN for first 4 entries

    # 20d rolling annualised return (cumulative)
    rolling_20_ret = (np.exp(rets_series.rolling(window=20, min_periods=5).sum()) - 1.0).values

    # Self-referential vol p60: percentile of all valid rolling_20 values
    valid_vols = rolling_20[~np.isnan(rolling_20)]
    if len(valid_vols) >= 5:
        vol_p60 = float(np.percentile(valid_vols, 60))
    else:
        vol_p60 = 0.15

    for i in range(n):
        if np.isnan(rolling_20[i]) or np.isnan(rolling_20_ret[i]):
            macro[i] = 1
            continue
        ret_20 = rolling_20_ret[i]
        vol_20 = rolling_20[i]
        if ret_20 > 0.05 and vol_20 <= vol_p60:
            macro[i] = 0
        elif ret_20 < -0.05 and vol_20 > vol_p60:
            macro[i] = 2
        else:
            macro[i] = 1

    # Self-transition matrix
    trans = np.zeros((3, 3), dtype=float)
    for k in range(1, n):
        a, b = int(macro[k - 1]), int(macro[k])
        trans[a, b] += 1
    for r in range(3):
        s = trans[r].sum()
        if s > 0:
            trans[r] = trans[r] / s

    # Current regime probability proxy: last 20 days
    if n >= 20:
        tail = macro[-20:]
    else:
        tail = macro
    probs = [
        ("Bull",     float((tail == 0).mean()), 0),
        ("Sideways", float((tail == 1).mean()), 1),
        ("Bear",     float((tail == 2).mean()), 2),
    ]
    return {"macros": macro.tolist(),
            "trans": trans.tolist(),
            "probs": probs}


def get_regime_data() -> dict:
    """Compute regime from real ^GSPC history. Falls back to SPA-mode when yfinance fails."""
    hist = get_index_history("^GSPC", days=95)
    if hist is None or len(hist) < 25:
        return _regime_fallback()
    closes = hist["close"].values
    returns = np.diff(np.log(closes))
    out = _classify_regimes(closes, returns)
    out["price_path"] = closes.tolist()
    out["macros"] = out["macros"]
    out["source"] = "yfinance-90d"
    return out


def _regime_fallback() -> dict:
    """SPA-mode fallback when yfinance ^GSPC fails."""
    return {
        "price_path": [],
        "macros": [],
        "trans": [[0.7, 0.2, 0.1],
                  [0.2, 0.6, 0.2],
                  [0.1, 0.2, 0.7]],
        "probs": [("Bull", 0.5, 0), ("Sideways", 0.3, 1), ("Bear", 0.2, 2)],
        "source": "fallback",
    }


# ------------------------------------------------------------------ #
# 4. Monte Carlo parameters from ^GSPC realised μ, σ
# ------------------------------------------------------------------ #
@st.cache_data(ttl=300, show_spinner=False)
def get_mc_params(symbol: str = "^GSPC", spot_signature: float = 0.0) -> dict:
    """Annualised μ, σ from last 60 trading days of log-returns.

    Cache key = (symbol, spot_signature/100). Bucket ``spot_signature`` by
    nearest $100 so slider drags of a few dollars don't cache-bust, but a
    real underlying switch (SPX → NDX) does.

    NOTE: NEITHER param is underscore-prefixed so Streamlit hashes both —
    critical for per-ticker cache separation.
    """
    sig_bucket = round(float(spot_signature), -2)
    hist = get_index_history(symbol=symbol, days=95)
    if hist is None or len(hist) < 30:
        return {"mu": 0.08, "sigma": 0.25, "start_value": None,
                "source": "fallback-fixed"}
    closes = hist["close"].values
    returns = np.diff(np.log(closes[-60:]))
    mu = float(np.mean(returns)) * 252.0
    sigma = float(np.std(returns, ddof=1)) * np.sqrt(252.0)
    return {"mu": mu, "sigma": sigma, "start_value": None,
            "source": "yfinance-60d"}


# ------------------------------------------------------------------ #
# 5. ATM IV from CBOE chain — for the Greeks tab default
# ------------------------------------------------------------------ #
def get_atm_iv(chain: Optional[pd.DataFrame], spot: float) -> float:
    """IV of the ATM call at the nearest-with-positive-OI expiry.

    Falls back to ``0.30`` when the chain is unusable.
    """
    if chain is None or chain.empty:
        return 0.30
    if not {"iv", "open_interest", "strike", "expiration", "type"}.issubset(set(chain.columns)):
        return 0.30
    df = chain.copy()
    df["_is_call"] = df["type"].astype(str).str.lower().str.startswith("c")
    calls = df[df["_is_call"] & (df["open_interest"] > 0) & (df["iv"] > 0)]
    if calls.empty:
        return 0.30
    now_ts = int(pd.Timestamp.utcnow().timestamp())
    calls = calls.copy()
    calls["_exp_ts"] = pd.to_datetime(calls["expiration"]).astype("int64") // 10**9
    future = calls[calls["_exp_ts"] >= now_ts]
    if future.empty:
        return 0.30
    # nearest expiry
    nearest_exp_ts = future["_exp_ts"].min()  # smallest-positive = nearest
    nearest_exp = future.loc[future["_exp_ts"] == nearest_exp_ts, "expiration"].iloc[0]
    near = future[future["expiration"] == nearest_exp].copy()
    if near.empty:
        return 0.30
    near["_dist"] = (near["strike"] - float(spot)).abs()
    return float(near.loc[near["_dist"].idxmin(), "iv"])


# Convenience helper: render a fallback deterministic smile identical
# to the original _smile() formula so the page has something to show if
# the CBOE chain is too thin.
def fallback_smile(K, T, S0=100.0):
    """Deterministic IV fallback matching the original _smile() formula."""
    logM = np.log(np.asarray(K) / S0)
    return 0.20 + 0.05 * logM ** 2 + 0.02 * logM + 0.03 * np.exp(-np.asarray(T) / 2.0)

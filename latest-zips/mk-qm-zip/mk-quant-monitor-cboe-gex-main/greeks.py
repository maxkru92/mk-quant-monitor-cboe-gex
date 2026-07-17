"""
Black-Scholes Greeks calculator.

Used when the data source does not provide Greeks (e.g. Yahoo Finance).
"""

from __future__ import annotations

import datetime as dt
from typing import Union

import numpy as np
import pandas as pd
from scipy.stats import norm


# ------------------------------------------------------------------ #
# Black-Scholes helpers
# ------------------------------------------------------------------ #
def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T + 1e-12))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return _d1(S, K, T, r, sigma) - sigma * np.sqrt(T + 1e-12)


def black_scholes_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "Call",
    q: float = 0.0,
) -> dict[str, float]:
    """
    Calculate Delta, Gamma, Theta, Vega, Rho for a single option.

    Parameters
    ----------
    S : float
        Spot price of the underlying.
    K : float
        Strike price.
    T : float
        Time to expiration in years (clamped to a minimum of 1/252).
    r : float
        Risk-free rate (annual).
    sigma : float
        Implied volatility (annual).
    option_type : str
        'Call' or 'Put'.
    q : float
        Continuous dividend yield (annual). Default 0.

    Returns
    -------
    dict
        {'delta': float, 'gamma': float, 'theta': float, 'vega': float, 'rho': float}
    """
    T = max(T, 1.0 / 252.0)
    is_call = str(option_type).lower().startswith("c")

    d1_ = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T + 1e-12))
    d2_ = d1_ - sigma * np.sqrt(T + 1e-12)

    nd1 = norm.cdf(d1_)
    nd2 = norm.cdf(d2_)
    n_d1 = norm.pdf(d1_)

    if is_call:
        delta = np.exp(-q * T) * nd1
        theta = (
            -S * np.exp(-q * T) * n_d1 * sigma / (2 * np.sqrt(T))
            - r * K * np.exp(-r * T) * nd2
            + q * S * np.exp(-q * T) * nd1
        ) / 252.0
        rho = K * T * np.exp(-r * T) * nd2 / 100.0
    else:
        delta = np.exp(-q * T) * (nd1 - 1.0)
        theta = (
            -S * np.exp(-q * T) * n_d1 * sigma / (2 * np.sqrt(T))
            + r * K * np.exp(-r * T) * (1 - nd2)
            - q * S * np.exp(-q * T) * (1 - nd1)
        ) / 252.0
        rho = -K * T * np.exp(-r * T) * (1 - nd2) / 100.0

    gamma = np.exp(-q * T) * n_d1 / (S * sigma * np.sqrt(T))
    vega = S * np.exp(-q * T) * n_d1 * np.sqrt(T) / 100.0

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "theta": float(theta),
        "vega": float(vega),
        "rho": float(rho),
    }


def add_greeks_to_chain(
    chain: pd.DataFrame,
    spot: float,
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.0,
    valuation_date: Union[dt.datetime, dt.date, None] = None,
) -> pd.DataFrame:
    """
    Add Black-Scholes Greeks to an options chain DataFrame.

    Expected columns: expiration, strike, type, iv
    """
    df = chain.copy()

    if valuation_date is None:
        valuation_date = dt.datetime.now()
    elif isinstance(valuation_date, dt.date):
        valuation_date = dt.datetime.combine(valuation_date, dt.time.min)

    df["expiration"] = pd.to_datetime(df["expiration"])
    df["T"] = (df["expiration"] - pd.Timestamp(valuation_date)).dt.total_seconds() / (
        365.25 * 24 * 3600
    )
    df["T"] = df["T"].clip(lower=1.0 / 252.0)

    greeks = df.apply(
        lambda row: black_scholes_greeks(
            S=spot,
            K=float(row["strike"]),
            T=float(row["T"]),
            r=risk_free_rate,
            sigma=float(row["iv"]),
            option_type=row["type"],
            q=dividend_yield,
        ),
        axis=1,
    )

    greeks_df = pd.DataFrame(greeks.tolist(), index=df.index)
    df = pd.concat([df, greeks_df], axis=1)
    df = df.drop(columns=["T"])
    return df


def annualised_iv_to_daily_move(spot: float, iv: float, trading_days: int = 252) -> float:
    """Return the 1-day expected move in price terms."""
    return spot * iv / np.sqrt(trading_days)

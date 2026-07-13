"""
GEX (Gamma Exposure) calculator.

Definitions used here:
- GEX per contract = Gamma * OI * Spot^2 * 0.01
- Net GEX per strike = Call GEX + Put GEX (Put GEX is negative by construction)
- Gamma Wall = strike with the largest absolute Net GEX
- Call Resistance = highest Call GEX strike above spot
- Put Support = lowest Put GEX strike below spot
- HVL (High Volume Level) = strike with the largest total gross gamma
"""

from __future__ import annotations

import datetime as dt
from typing import Optional, Tuple

import numpy as np
import pandas as pd


class GEXCalculator:
    """Calculate Gamma Exposure and related levels from an options chain."""

    def __init__(self, chain: pd.DataFrame, spot: float):
        self.chain = chain.copy()
        self.spot = float(spot)
        self._validate()

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate(self) -> None:
        required = {"strike", "type", "open_interest", "gamma"}
        missing = required - set(self.chain.columns)
        if missing:
            raise ValueError(f"GEXCalculator missing required columns: {missing}")

    # ------------------------------------------------------------------ #
    # Core calculations
    # ------------------------------------------------------------------ #
    def calculate_gex(self) -> pd.DataFrame:
        """Return chain with GEX column added."""
        df = self.chain.copy()
        df["gex"] = (
            df["gamma"]
            * df["open_interest"]
            * (self.spot ** 2)
            * 0.01
        )
        # Put gamma is positive in chain; flip sign for GEX convention
        df.loc[df["type"].str.lower().str.startswith("p"), "gex"] *= -1
        return df

    def gex_by_strike(self, chain: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """Aggregate GEX by strike."""
        df = chain if chain is not None else self.calculate_gex()
        calls = df[df["type"].str.lower().str.startswith("c")].groupby("strike").agg(
            call_gex=("gex", "sum"),
            call_oi=("open_interest", "sum"),
            call_volume=("volume", "sum"),
        )
        puts = df[df["type"].str.lower().str.startswith("p")].groupby("strike").agg(
            put_gex=("gex", "sum"),
            put_oi=("open_interest", "sum"),
            put_volume=("volume", "sum"),
        )

        by_strike = calls.join(puts, how="outer").fillna(0)
        by_strike["net_gex"] = by_strike["call_gex"] + by_strike["put_gex"]
        by_strike["abs_gex"] = by_strike["net_gex"].abs()
        by_strike["gross_gamma"] = by_strike["call_gex"].abs() + by_strike["put_gex"].abs()
        by_strike["total_oi"] = by_strike["call_oi"] + by_strike["put_oi"]
        by_strike["total_volume"] = by_strike["call_volume"] + by_strike["put_volume"]
        return by_strike.sort_index()

    # ------------------------------------------------------------------ #
    # MenthorQ-style levels
    # ------------------------------------------------------------------ #
    def levels(self, chain: Optional[pd.DataFrame] = None) -> dict:
        """Return all MenthorQ-style levels for the full chain."""
        by_strike = self.gex_by_strike(chain)
        return self._levels_from_by_strike(by_strike)

    def levels_0dte(self, chain: Optional[pd.DataFrame] = None) -> dict:
        """Return all MenthorQ-style levels for 0DTE options only."""
        import pytz
        df = chain if chain is not None else self.calculate_gex()
        eastern = pytz.timezone("US/Eastern")
        today = pd.Timestamp.now(tz=eastern).normalize().tz_localize(None)
        df["expiration"] = pd.to_datetime(df["expiration"]).dt.tz_localize(None)
        dte = df[(df["expiration"] >= today) & (df["expiration"] < today + pd.Timedelta(days=1))]
        if dte.empty:
            return {}
        by_strike = self.gex_by_strike(dte)
        return self._levels_from_by_strike(by_strike)

    def _levels_from_by_strike(self, by_strike: pd.DataFrame) -> dict:
        if by_strike.empty:
            return {}

        above = by_strike[by_strike.index > self.spot]
        below = by_strike[by_strike.index < self.spot]

        # Call Resistance: highest call GEX strike above current spot
        call_resistance = float(above["call_gex"].idxmax()) if not above.empty else float(by_strike["call_gex"].idxmax())

        # Put Support: lowest (most negative) put GEX strike below current spot
        put_support = float(below["put_gex"].idxmin()) if not below.empty else float(by_strike["put_gex"].idxmin())

        # HVL (High Volume Level): strike with the highest total volume + OI
        by_strike["hvl_score"] = by_strike["total_volume"] + by_strike["total_oi"]
        hvl = float(by_strike["hvl_score"].idxmax())

        # Gamma Wall: highest absolute net GEX
        gamma_wall = float(by_strike["abs_gex"].idxmax())

        # Top 10 GEX strikes by absolute net GEX
        top10 = by_strike.sort_values("abs_gex", ascending=False).head(10)
        gex_levels = [float(idx) for idx in top10.index]

        return {
            "call_resistance": call_resistance,
            "put_support": put_support,
            "hvl": hvl,
            "gamma_wall": gamma_wall,
            "gex_levels": gex_levels,
        }

    # ------------------------------------------------------------------ #
    # Expected move
    # ------------------------------------------------------------------ #
    def expected_move_1d(self, iv: Optional[float] = None) -> Tuple[float, float, float]:
        """Return (move, min, max) for a 1-day expected move."""
        if iv is None:
            # Use median ATM-ish IV from the chain
            df = self.chain.copy()
            if df.empty:
                return 0.0, self.spot, self.spot
            df["distance"] = (df["strike"] - self.spot).abs()
            atm = df.loc[df["distance"].idxmin()]
            iv = float(atm["iv"])

        move = self.spot * iv / np.sqrt(252)
        return move, self.spot - move, self.spot + move

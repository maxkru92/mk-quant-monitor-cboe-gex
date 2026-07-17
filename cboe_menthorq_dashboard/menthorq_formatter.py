"""
MenthorQ-style output string formatter.

Produces a single-line data string with gamma / GEX levels exactly like:

$SPX: Call Resistance, 7600, Put Support, 7300, HVL, 7495, 1D Min, 7451.62, 1D Max, 7580.18,
Call Resistance 0DTE, 7550, Put Support 0DTE, 7475, HVL 0DTE, 7530, Gamma Wall 0DTE, 7550,
GEX 1, 7500, GEX 2, 7575, ...
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class MenthorQString:
    """Build and format a MenthorQ-style gamma data string."""

    def __init__(
        self,
        symbol: str,
        spot: float,
        levels: Dict[str, Any],
        levels_0dte: Optional[Dict[str, Any]] = None,
        min_1d: Optional[float] = None,
        max_1d: Optional[float] = None,
    ):
        self.symbol = symbol.upper()
        self.spot = float(spot)
        self.levels = levels
        self.levels_0dte = levels_0dte or {}
        self.min_1d = min_1d
        self.max_1d = max_1d

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fmt(value: Any, decimals: int = 2) -> str:
        """Format a numeric value for the TradingView indicator string.

        - Integers → no decimal point:  ``14``, ``7600``
        - Floats → stripped trailing zeros: ``19.5``, ``14.57`` (never ``19.50``)
        """
        if value is None:
            return "N/A"
        try:
            num = float(value)
            if num == int(num):
                return str(int(num))
            # Remove trailing zeros so ``19.50`` → ``19.5``
            formatted = f"{num:.{decimals}f}"
            return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
        except (TypeError, ValueError):
            return str(value)

    # ------------------------------------------------------------------ #
    # Build string
    # ------------------------------------------------------------------ #
    def build(self) -> str:
        """Return the formatted MenthorQ data string."""
        parts: List[str] = [f"${self.symbol}:"]

        # Full-chain levels
        parts += [
            "Call Resistance",
            self._fmt(self.levels.get("call_resistance")),
            "Put Support",
            self._fmt(self.levels.get("put_support")),
            "HVL",
            self._fmt(self.levels.get("hvl")),
        ]

        # 1D expected move
        if self.min_1d is not None and self.max_1d is not None:
            parts += [
                "1D Min",
                self._fmt(self.min_1d),
                "1D Max",
                self._fmt(self.max_1d),
            ]
        else:
            parts += ["1D Min", "N/A", "1D Max", "N/A"]

        # 0DTE levels
        if self.levels_0dte:
            parts += [
                "Call Resistance 0DTE",
                self._fmt(self.levels_0dte.get("call_resistance")),
                "Put Support 0DTE",
                self._fmt(self.levels_0dte.get("put_support")),
                "HVL 0DTE",
                self._fmt(self.levels_0dte.get("hvl")),
                "Gamma Wall 0DTE",
                self._fmt(self.levels_0dte.get("gamma_wall")),
            ]
        else:
            parts += [
                "Call Resistance 0DTE",
                "N/A",
                "Put Support 0DTE",
                "N/A",
                "HVL 0DTE",
                "N/A",
                "Gamma Wall 0DTE",
                "N/A",
            ]

        # Top 10 GEX levels from full chain
        gex_levels = self.levels.get("gex_levels", [])
        for i, strike in enumerate(gex_levels[:10], start=1):
            parts += [f"GEX {i}", self._fmt(strike)]

        return ", ".join(parts)

    def __str__(self) -> str:
        return self.build()

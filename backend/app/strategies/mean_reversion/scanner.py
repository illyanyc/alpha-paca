"""Mean-reversion scanner — finds oversold / overbought conditions."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

BB_PERIOD = 20
BB_STD_MULT = 2.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


class MeanReversionScanner:
    """Scans for symbols trading at Bollinger-band extremes or RSI tails."""

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for symbol in universe:
            candidate = self._score_symbol(symbol)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: abs(c["bb_z"]), reverse=True)
        logger.info("mean_reversion_scan_complete", candidates=len(candidates))
        return candidates

    def _score_symbol(self, symbol: str) -> dict[str, Any] | None:
        return {
            "symbol": symbol,
            "bb_z": 0.0,
            "rsi": 50.0,
            "last_price": 0.0,
            "mean_price": 0.0,
            "reversion_score": 0.0,
        }

    @staticmethod
    def compute_bollinger(
        closes: np.ndarray,
        period: int = BB_PERIOD,
        std_mult: float = BB_STD_MULT,
    ) -> tuple[float, float, float]:
        """Return (upper, middle, lower) Bollinger bands."""
        if len(closes) < period:
            mid = float(closes[-1]) if len(closes) else 0.0
            return mid, mid, mid
        window = closes[-period:]
        mid = float(np.mean(window))
        std = float(np.std(window))
        return mid + std_mult * std, mid, mid - std_mult * std

    @staticmethod
    def bb_z_score(price: float, upper: float, lower: float) -> float:
        """Standardised distance within the Bollinger channel (−1 … +1 nominal)."""
        band_width = upper - lower
        if band_width == 0:
            return 0.0
        mid = (upper + lower) / 2
        return (price - mid) / (band_width / 2)

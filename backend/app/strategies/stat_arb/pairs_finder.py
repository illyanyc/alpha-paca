"""Cointegration testing for pairs trading."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

COINT_P_VALUE_THRESHOLD = 0.05
MIN_HALF_LIFE = 1
MAX_HALF_LIFE = 60


class PairsFinder:
    """Finds cointegrated pairs from a universe of symbols."""

    def find_pairs(
        self,
        universe: list[str],
    ) -> list[dict[str, Any]]:
        """Test all unique pairs for cointegration.

        In production this loads historical price data and runs the
        Engle-Granger test.  The skeleton here demonstrates the interface.
        """
        pairs: list[dict[str, Any]] = []
        for i in range(len(universe)):
            for j in range(i + 1, len(universe)):
                result = self._test_pair(universe[i], universe[j])
                if result is not None:
                    pairs.append(result)
        logger.info("pairs_finder_complete", tested=len(universe), found=len(pairs))
        return pairs

    def _test_pair(
        self,
        symbol_a: str,
        symbol_b: str,
    ) -> dict[str, Any] | None:
        """Placeholder — real implementation runs ADF on the spread residuals."""
        return {
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "p_value": 1.0,
            "half_life": 0.0,
            "hedge_ratio": 1.0,
        }

    @staticmethod
    def compute_half_life(spread: np.ndarray) -> float:
        """Mean-reversion half-life via OLS on lagged spread."""
        if len(spread) < 3:
            return float("inf")
        lag = spread[:-1]
        delta = np.diff(spread)
        lag_mean = lag - np.mean(lag)
        denom = np.dot(lag_mean, lag_mean)
        if denom == 0:
            return float("inf")
        beta = np.dot(lag_mean, delta) / denom
        if beta >= 0:
            return float("inf")
        return float(-np.log(2) / beta)

    @staticmethod
    def compute_hedge_ratio(
        prices_a: np.ndarray,
        prices_b: np.ndarray,
    ) -> float:
        """OLS hedge ratio: regress A on B."""
        if len(prices_a) < 2:
            return 1.0
        b_mean = prices_b - np.mean(prices_b)
        denom = np.dot(b_mean, b_mean)
        if denom == 0:
            return 1.0
        return float(np.dot(b_mean, prices_a - np.mean(prices_a)) / denom)

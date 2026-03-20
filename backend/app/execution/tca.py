"""Transaction Cost Analysis (TCA)."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class TCAAnalyzer:
    """Measures execution quality via implementation shortfall and market impact."""

    @staticmethod
    def compute_implementation_shortfall(
        expected_price: float,
        avg_fill_price: float,
        qty: float,
    ) -> float:
        """Dollar-value implementation shortfall (positive = cost)."""
        return (avg_fill_price - expected_price) * qty

    @staticmethod
    def compute_market_impact(
        order_size: float,
        avg_daily_volume: float,
    ) -> float:
        """Estimated market impact as a fraction (square-root model).

        impact ≈ 0.1 × sqrt(order_size / ADV)
        """
        if avg_daily_volume <= 0:
            return 0.0
        participation = order_size / avg_daily_volume
        return 0.1 * float(np.sqrt(participation))

    @classmethod
    def generate_tca_report(
        cls,
        trades: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate TCA metrics across a list of trades."""
        if not trades:
            return {
                "trade_count": 0,
                "total_shortfall": 0.0,
                "avg_slippage_bps": 0.0,
                "avg_market_impact": 0.0,
            }

        shortfalls: list[float] = []
        slippages: list[float] = []
        impacts: list[float] = []

        for t in trades:
            expected = t.get("expected_price", t.get("entry_price", 0.0))
            fill = t.get("avg_fill_price", t.get("fill_price", expected))
            qty = t.get("qty", 0.0)
            adv = t.get("avg_daily_volume", 0.0)

            shortfalls.append(cls.compute_implementation_shortfall(expected, fill, qty))

            if expected > 0:
                slippages.append(abs(fill - expected) / expected * 10_000)

            if adv > 0:
                impacts.append(cls.compute_market_impact(abs(qty * fill), adv))

        return {
            "trade_count": len(trades),
            "total_shortfall": float(np.sum(shortfalls)),
            "avg_slippage_bps": float(np.mean(slippages)) if slippages else 0.0,
            "avg_market_impact": float(np.mean(impacts)) if impacts else 0.0,
        }

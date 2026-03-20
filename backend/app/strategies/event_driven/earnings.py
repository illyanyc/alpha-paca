"""Earnings surprise analysis utilities."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EarningsAnalyzer:
    """Analyses earnings announcements for tradeable surprises."""

    @staticmethod
    def compute_surprise_pct(
        actual_eps: float,
        estimated_eps: float,
    ) -> float:
        """Percentage earnings surprise relative to consensus."""
        if estimated_eps == 0:
            return 0.0
        return ((actual_eps - estimated_eps) / abs(estimated_eps)) * 100

    @staticmethod
    def classify_reaction(
        surprise_pct: float,
        price_change_pct: float,
    ) -> str:
        """Classify post-earnings price action.

        Returns one of: ``beat_rally``, ``beat_sell``, ``miss_sell``, ``miss_rally``,
        or ``neutral``.
        """
        beat = surprise_pct > 2
        miss = surprise_pct < -2
        up = price_change_pct > 1
        down = price_change_pct < -1

        if beat and up:
            return "beat_rally"
        if beat and down:
            return "beat_sell"
        if miss and down:
            return "miss_sell"
        if miss and up:
            return "miss_rally"
        return "neutral"

    @staticmethod
    def score_earnings_event(
        surprise_pct: float,
        reaction: str,
        historical_surprises: list[float] | None = None,
    ) -> float:
        """Score an earnings event from 0.0 to 1.0 for signal strength."""
        score = min(abs(surprise_pct) / 20.0, 0.5)

        if reaction in ("beat_rally", "miss_sell"):
            score += 0.3
        elif reaction in ("beat_sell", "miss_rally"):
            score += 0.1

        if historical_surprises and len(historical_surprises) >= 3:
            streak = all(s > 0 for s in historical_surprises[-3:]) or all(
                s < 0 for s in historical_surprises[-3:]
            )
            if streak:
                score += 0.2

        return min(score, 1.0)

"""Factor exposure limit monitoring."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class FactorExposureMonitor:
    """Compares current factor exposures against configurable limits."""

    @staticmethod
    def check_limits(
        exposures: dict[str, float],
        limits: dict[str, float],
    ) -> bool:
        """Return ``True`` if all exposures are within limits."""
        for factor, limit in limits.items():
            current = abs(exposures.get(factor, 0.0))
            if current > limit:
                logger.warning(
                    "factor_limit_breached",
                    factor=factor,
                    current=current,
                    limit=limit,
                )
                return False
        return True

    @staticmethod
    def get_breach_report(
        exposures: dict[str, float],
        limits: dict[str, float],
    ) -> list[dict[str, float | str]]:
        """Return a list of breached factors with current value and limit."""
        breaches: list[dict[str, float | str]] = []
        for factor, limit in limits.items():
            current = abs(exposures.get(factor, 0.0))
            if current > limit:
                breaches.append(
                    {"factor": factor, "current": current, "limit": limit}
                )
        return breaches

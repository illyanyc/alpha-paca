"""Pod overlap monitoring — return correlation and holdings similarity."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class PodOverlapMonitor:
    """Detects excessive overlap between strategy pods."""

    @staticmethod
    def compute_return_correlation(
        returns_a: np.ndarray,
        returns_b: np.ndarray,
    ) -> float:
        """Pearson correlation of daily return series."""
        if len(returns_a) < 2 or len(returns_b) < 2:
            return 0.0
        mask = ~(np.isnan(returns_a) | np.isnan(returns_b))
        a, b = returns_a[mask], returns_b[mask]
        if len(a) < 2:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    @staticmethod
    def compute_holdings_overlap(
        holdings_a: set[str],
        holdings_b: set[str],
    ) -> float:
        """Jaccard similarity between two sets of held symbols."""
        if not holdings_a and not holdings_b:
            return 0.0
        intersection = holdings_a & holdings_b
        union = holdings_a | holdings_b
        return len(intersection) / len(union)

    @classmethod
    def check_overlap_limits(
        cls,
        pods: dict[str, dict[str, Any]],
        max_corr: float = 0.60,
    ) -> list[dict[str, Any]]:
        """Flag all pod pairs whose return correlation exceeds ``max_corr``.

        ``pods`` maps pod_name -> {"returns": np.ndarray, "holdings": set[str]}.
        """
        breaches: list[dict[str, Any]] = []
        names = list(pods.keys())

        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                corr = cls.compute_return_correlation(
                    pods[a]["returns"], pods[b]["returns"]
                )
                if corr > max_corr:
                    breaches.append(
                        {"pod_a": a, "pod_b": b, "correlation": corr, "limit": max_corr}
                    )
                    logger.warning(
                        "pod_overlap_breach",
                        pod_a=a,
                        pod_b=b,
                        correlation=corr,
                    )
        return breaches

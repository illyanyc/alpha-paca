"""Value-at-Risk computation (historical and parametric)."""

from __future__ import annotations

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class VaREngine:
    """Portfolio VaR / CVaR calculator."""

    @staticmethod
    def compute_var(
        returns: np.ndarray,
        confidence: float = 0.95,
    ) -> float:
        """Historical VaR at the given confidence level.

        Returns the loss threshold (positive number) such that losses exceed
        this value only ``(1 - confidence)`` of the time.
        """
        if len(returns) == 0:
            return 0.0
        percentile = (1 - confidence) * 100
        return float(-np.percentile(returns, percentile))

    @staticmethod
    def compute_cvar(
        returns: np.ndarray,
        confidence: float = 0.95,
    ) -> float:
        """Conditional VaR (Expected Shortfall) — mean loss beyond the VaR threshold."""
        if len(returns) == 0:
            return 0.0
        percentile = (1 - confidence) * 100
        threshold = np.percentile(returns, percentile)
        tail = returns[returns <= threshold]
        if len(tail) == 0:
            return float(-threshold)
        return float(-np.mean(tail))

    @staticmethod
    def parametric_var(
        positions: np.ndarray,
        cov_matrix: np.ndarray,
        confidence: float = 0.95,
    ) -> float:
        """Parametric (variance-covariance) VaR.

        ``positions``  — vector of position dollar values.
        ``cov_matrix`` — asset return covariance matrix.
        """
        from scipy.stats import norm  # type: ignore[import-untyped]

        port_var = float(positions @ cov_matrix @ positions)
        port_std = np.sqrt(port_var)
        z = norm.ppf(confidence)
        return float(z * port_std)

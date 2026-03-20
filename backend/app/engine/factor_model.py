"""Factor model for risk decomposition."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

FACTOR_NAMES: list[str] = [
    "market_beta",
    "size",
    "value",
    "momentum",
    "quality",
    "low_vol",
]


class FactorModel:
    """Computes portfolio-level factor exposures and risk contributions."""

    def compute_factor_exposures(
        self,
        positions: list[dict[str, Any]],
    ) -> dict[str, float]:
        """Calculate aggregate factor exposures from open positions.

        Each position dict is expected to carry a ``factor_exposures`` mapping
        with per-factor loadings and a ``weight`` (fraction of portfolio).
        Missing factors default to 0.
        """
        exposures = {f: 0.0 for f in FACTOR_NAMES}
        if not positions:
            return exposures

        total_weight = sum(abs(p.get("weight", 0.0)) for p in positions)
        if total_weight == 0:
            return exposures

        for pos in positions:
            w = pos.get("weight", 0.0) / total_weight
            fe = pos.get("factor_exposures") or {}
            for factor in FACTOR_NAMES:
                exposures[factor] += w * fe.get(factor, 0.0)

        logger.info("factor_exposures_computed", exposures=exposures)
        return exposures

    def compute_factor_risk(
        self,
        exposures: dict[str, float],
    ) -> dict[str, float]:
        """Estimate each factor's contribution to total portfolio risk.

        Uses squared exposure as a proxy when a full covariance matrix is
        unavailable.
        """
        total_sq = sum(e ** 2 for e in exposures.values()) or 1.0
        return {
            factor: (exp ** 2) / total_sq
            for factor, exp in exposures.items()
        }

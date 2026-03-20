"""Stress testing engine with predefined market-shock scenarios."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

SCENARIOS: dict[str, dict[str, float]] = {
    "2008_financial_crisis": {
        "equity_shock": -0.40,
        "vol_spike": 3.0,
        "credit_spread_widen_bps": 500,
        "description_factor": -0.55,
    },
    "covid_march_2020": {
        "equity_shock": -0.34,
        "vol_spike": 4.0,
        "credit_spread_widen_bps": 300,
        "description_factor": -0.35,
    },
    "rate_shock_300bps": {
        "equity_shock": -0.15,
        "vol_spike": 1.5,
        "rate_change_bps": 300,
        "description_factor": -0.18,
    },
    "flash_crash": {
        "equity_shock": -0.10,
        "vol_spike": 5.0,
        "description_factor": -0.12,
    },
    "sector_rotation": {
        "equity_shock": -0.08,
        "momentum_reversal": -0.20,
        "description_factor": -0.10,
    },
}


class StressTestRunner:
    """Applies predefined shock scenarios to a portfolio of positions."""

    @staticmethod
    def run_scenario(
        positions: list[dict[str, Any]],
        scenario: dict[str, float],
    ) -> dict[str, float]:
        """Apply a single scenario and estimate dollar / percentage loss.

        Each position dict should include ``market_value`` and optionally
        ``beta`` for equity-shock scaling.
        """
        total_mv = sum(p.get("market_value", 0.0) for p in positions)
        if total_mv == 0:
            return {"loss_pct": 0.0, "loss_dollars": 0.0, "positions_impacted": 0}

        equity_shock = scenario.get("equity_shock", 0.0)
        impacted = 0
        total_loss = 0.0

        for pos in positions:
            mv = pos.get("market_value", 0.0)
            beta = pos.get("beta", 1.0)
            pos_loss = mv * equity_shock * beta
            total_loss += pos_loss
            if pos_loss != 0:
                impacted += 1

        loss_pct = total_loss / total_mv if total_mv else 0.0
        return {
            "loss_pct": float(loss_pct),
            "loss_dollars": float(total_loss),
            "positions_impacted": impacted,
        }

    @classmethod
    def run_all(
        cls,
        positions: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Run every predefined scenario and return results keyed by name."""
        results: dict[str, dict[str, float]] = {}
        for name, scenario in SCENARIOS.items():
            results[name] = cls.run_scenario(positions, scenario)
            logger.info(
                "stress_test_completed",
                scenario=name,
                loss_pct=results[name]["loss_pct"],
            )
        return results

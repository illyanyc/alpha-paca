"""RL meta-controller for dynamic pod allocation using PPO."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import structlog

from app.engine.regime.models import RegimeState, REGIME_POD_WEIGHTS

logger = structlog.get_logger(__name__)

MODEL_PATH = Path(__file__).parent / "trained_model.zip"
N_PODS = 5
POD_NAMES = ["momentum", "mean_reversion", "event_driven", "sector_rotation", "stat_arb"]


class RLMetaController:
    """Wraps a trained PPO model to determine optimal pod allocation weights.

    Falls back to the static regime-based allocation table when no trained
    model is available or stable-baselines3 is not installed.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._load_model()

    def get_allocations(
        self,
        regime_probs: list[float],
        pod_sharpes: list[float],
        pod_drawdowns: list[float],
        vix: float = 20.0,
        days_since_transition: int = 0,
        gross_exposure: float = 0.0,
    ) -> dict[str, float]:
        """Return pod allocation weights (summing to 1.0) and risk scaling factor."""
        if self._model is not None:
            try:
                obs = np.array(
                    regime_probs
                    + pod_sharpes
                    + pod_drawdowns
                    + [vix / 100, days_since_transition / 30, gross_exposure / 100],
                    dtype=np.float32,
                )
                action, _ = self._model.predict(obs, deterministic=True)
                weights = action[:N_PODS]
                weight_sum = weights.sum()
                if weight_sum > 0:
                    weights = weights / weight_sum
                else:
                    weights = np.ones(N_PODS) / N_PODS
                risk_scale = float(np.clip(action[N_PODS] * 1.25 + 0.25, 0.25, 1.5))
                alloc = {name: float(w) for name, w in zip(POD_NAMES, weights)}
                alloc["_risk_scale"] = risk_scale
                return alloc
            except Exception:
                logger.warning("rl_prediction_failed_using_fallback")

        return self._static_fallback(regime_probs)

    def _static_fallback(self, regime_probs: list[float]) -> dict[str, float]:
        """Fall back to the static regime-to-allocation table."""
        states = list(RegimeState)
        alloc: dict[str, float] = {name: 0.0 for name in POD_NAMES}
        total = 0.0
        for state, prob in zip(states, regime_probs):
            weights = REGIME_POD_WEIGHTS.get(state, {})
            for name in POD_NAMES:
                w = weights.get(name, 1.0) * prob
                alloc[name] += w
                total += w
        if total > 0:
            for name in POD_NAMES:
                alloc[name] /= total
        else:
            for name in POD_NAMES:
                alloc[name] = 1.0 / N_PODS
        alloc["_risk_scale"] = 1.0
        return alloc

    def _load_model(self) -> None:
        if not MODEL_PATH.exists():
            logger.info("rl_no_trained_model_using_static_allocation")
            return
        try:
            from stable_baselines3 import PPO
            self._model = PPO.load(str(MODEL_PATH))
            logger.info("rl_model_loaded", path=str(MODEL_PATH))
        except ImportError:
            logger.warning("stable_baselines3_not_installed_using_static_allocation")
        except Exception:
            logger.exception("rl_model_load_failed")

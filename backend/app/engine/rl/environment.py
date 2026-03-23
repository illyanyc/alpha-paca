"""Custom gymnasium environment for portfolio pod allocation."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

N_PODS = 5
N_REGIME_STATES = 4

try:
    import gymnasium as gym
    from gymnasium import spaces
    HAS_GYM = True
except ImportError:
    HAS_GYM = False
    gym = None
    spaces = None


if HAS_GYM:
    class PortfolioAllocEnv(gym.Env):
        """Environment where the agent allocates capital across trading pods.

        State space (17 dims):
            - Regime probabilities (4)
            - Rolling 30d Sharpe per pod (5)
            - Rolling 30d max drawdown per pod (5)
            - VIX level (1)
            - Days since last regime transition (1)
            - Total gross exposure % (1)

        Action space (6 dims):
            - Pod weights (5 continuous, softmax-normalized to sum to 1.0)
            - Risk scaling factor (1, bounded [0.25, 1.5])
        """

        metadata = {"render_modes": []}

        def __init__(self, historical_data: list[dict[str, Any]] | None = None) -> None:
            super().__init__()
            self._data = historical_data or []
            self._step_idx = 0
            self._prev_nav = 1_000_000.0

            obs_dim = N_REGIME_STATES + N_PODS * 2 + 3
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
            )
            self.action_space = spaces.Box(
                low=0.0, high=1.0, shape=(N_PODS + 1,), dtype=np.float32
            )

        def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
            super().reset(seed=seed)
            self._step_idx = 0
            self._prev_nav = 1_000_000.0
            obs = self._get_obs()
            return obs, {}

        def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
            weights = action[:N_PODS]
            weight_sum = weights.sum()
            if weight_sum > 0:
                weights = weights / weight_sum
            else:
                weights = np.ones(N_PODS) / N_PODS

            risk_scale = np.clip(action[N_PODS] * 1.25 + 0.25, 0.25, 1.5)

            self._step_idx += 1
            done = self._step_idx >= len(self._data) - 1

            reward = self._compute_reward(weights, risk_scale)
            obs = self._get_obs()

            return obs, reward, done, False, {"weights": weights.tolist(), "risk_scale": float(risk_scale)}

        def _get_obs(self) -> np.ndarray:
            if self._step_idx < len(self._data):
                row = self._data[self._step_idx]
                regime_probs = row.get("regime_probs", [0.25] * N_REGIME_STATES)
                pod_sharpes = row.get("pod_sharpes", [0.0] * N_PODS)
                pod_drawdowns = row.get("pod_drawdowns", [0.0] * N_PODS)
                vix = row.get("vix", 20.0)
                days_since_transition = row.get("days_since_transition", 0)
                gross_exposure = row.get("gross_exposure", 0.0)
            else:
                regime_probs = [0.25] * N_REGIME_STATES
                pod_sharpes = [0.0] * N_PODS
                pod_drawdowns = [0.0] * N_PODS
                vix = 20.0
                days_since_transition = 0
                gross_exposure = 0.0

            obs = np.array(
                regime_probs + pod_sharpes + pod_drawdowns + [vix / 100, days_since_transition / 30, gross_exposure / 100],
                dtype=np.float32,
            )
            return obs

        def _compute_reward(self, weights: np.ndarray, risk_scale: float) -> float:
            if self._step_idx >= len(self._data):
                return 0.0

            row = self._data[self._step_idx]
            pod_returns = row.get("pod_returns", [0.0] * N_PODS)
            portfolio_return = float(np.dot(weights, pod_returns)) * risk_scale

            nav = self._prev_nav * (1 + portfolio_return / 100)
            daily_return = (nav - self._prev_nav) / self._prev_nav
            self._prev_nav = nav

            reward = daily_return * 100

            drawdown = row.get("drawdown_pct", 0.0)
            if drawdown > 2.0:
                reward -= (drawdown - 2.0) * 0.5

            pod_corr = row.get("pod_correlation", 0.0)
            if pod_corr > 0.5:
                reward -= (pod_corr - 0.5) * 0.3

            market_return = row.get("market_return", 0.0)
            if market_return < -1.0 and portfolio_return > 0:
                reward += 0.5

            return float(reward)

else:
    class PortfolioAllocEnv:
        """Stub when gymnasium is not installed."""
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            logger.warning("gymnasium_not_installed_rl_controller_disabled")

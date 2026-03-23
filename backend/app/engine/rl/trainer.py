"""Offline training script for the RL meta-controller."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

MODEL_PATH = Path(__file__).parent / "trained_model.zip"


def generate_synthetic_data(n_episodes: int = 50, steps_per_episode: int = 252) -> list[dict[str, Any]]:
    """Generate synthetic training data mimicking market conditions."""
    rng = np.random.default_rng(42)
    data: list[dict[str, Any]] = []

    for _ in range(n_episodes):
        regime_idx = rng.integers(0, 4)
        regime_probs = [0.1] * 4
        regime_probs[regime_idx] = 0.7

        for step in range(steps_per_episode):
            if rng.random() < 0.02:
                regime_idx = rng.integers(0, 4)
                regime_probs = [0.1] * 4
                regime_probs[regime_idx] = 0.7

            base_returns = {
                0: [0.08, -0.02, 0.04, 0.03, 0.02],
                1: [-0.06, -0.01, -0.02, 0.04, 0.01],
                2: [0.01, 0.05, 0.02, 0.04, 0.01],
                3: [-0.10, 0.01, -0.03, 0.03, 0.08],
            }

            pod_returns = [
                r + rng.normal(0, 0.5) for r in base_returns[regime_idx]
            ]

            data.append({
                "regime_probs": regime_probs.copy(),
                "pod_sharpes": [rng.normal(0.5, 0.3) for _ in range(5)],
                "pod_drawdowns": [abs(rng.normal(2, 1)) for _ in range(5)],
                "pod_returns": pod_returns,
                "vix": max(10, rng.normal(20 + regime_idx * 5, 5)),
                "days_since_transition": step % 30,
                "gross_exposure": rng.uniform(50, 120),
                "drawdown_pct": abs(rng.normal(1, 1)),
                "pod_correlation": rng.uniform(0.1, 0.6),
                "market_return": rng.normal(0, 1),
            })

    return data


def train(total_timesteps: int = 100_000) -> None:
    """Train a PPO model on synthetic portfolio data."""
    try:
        from stable_baselines3 import PPO
        from app.engine.rl.environment import PortfolioAllocEnv
    except ImportError:
        logger.error("stable_baselines3_or_gymnasium_not_installed")
        return

    data = generate_synthetic_data()
    env = PortfolioAllocEnv(historical_data=data)

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=3e-4,
        n_steps=256,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        verbose=1,
    )

    logger.info("rl_training_started", timesteps=total_timesteps)
    model.learn(total_timesteps=total_timesteps)
    model.save(str(MODEL_PATH))
    logger.info("rl_training_complete", model_path=str(MODEL_PATH))


if __name__ == "__main__":
    train()

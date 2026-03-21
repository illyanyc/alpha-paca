"""Adaptive learning loop — tracks trade outcomes and adjusts strategy weights over time."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

LEARNINGS_KEY = "crypto:learner:state"
LEARNINGS_TTL = 86400 * 7  # 7 days


class AdaptiveLearner:
    """Tracks which strategies led to winning/losing trades and shifts weight accordingly.

    Uses an exponential moving average of per-strategy PnL to reward winners
    and punish losers, while always keeping minimum allocation to every strategy.
    """

    def __init__(self) -> None:
        self._trade_history: list[dict] = []
        self._strategy_scores: dict[str, float] = {}
        self._alpha = 0.3  # EMA smoothing factor — higher = more reactive

    @property
    def strategy_scores(self) -> dict[str, float]:
        return dict(self._strategy_scores)

    async def load(self, redis_conn: aioredis.Redis) -> None:
        raw = await redis_conn.get(LEARNINGS_KEY)
        if raw:
            try:
                state = json.loads(raw)
                self._trade_history = state.get("trade_history", [])[-200:]
                self._strategy_scores = state.get("strategy_scores", {})
                logger.info("learner_loaded", trades=len(self._trade_history), strategies=len(self._strategy_scores))
            except (json.JSONDecodeError, TypeError):
                pass

    async def save(self, redis_conn: aioredis.Redis) -> None:
        state = {
            "trade_history": self._trade_history[-200:],
            "strategy_scores": self._strategy_scores,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis_conn.set(LEARNINGS_KEY, json.dumps(state), ex=LEARNINGS_TTL)

    def record_trade(
        self,
        pair: str,
        side: str,
        pnl_pct: float,
        strategy_signals: dict[str, dict],
        confidence: float,
    ) -> None:
        """Record a completed trade and update strategy scores.

        strategy_signals: {strategy_name: {signal, score, confidence}} — the signals
        that were active when the trade was entered.
        """
        entry = {
            "pair": pair,
            "side": side,
            "pnl_pct": pnl_pct,
            "confidence": confidence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategies": {},
        }

        for name, sig in strategy_signals.items():
            was_correct = (
                (sig.get("signal") == "buy" and pnl_pct > 0) or
                (sig.get("signal") == "sell" and pnl_pct < 0)
            )
            entry["strategies"][name] = {
                "signal": sig.get("signal", "neutral"),
                "score": sig.get("score", 0),
                "correct": was_correct,
            }

            old = self._strategy_scores.get(name, 0.5)
            reward = 1.0 if was_correct else -0.5
            weighted_reward = reward * min(abs(pnl_pct) / 2.0, 1.0)
            self._strategy_scores[name] = old + self._alpha * (weighted_reward - (old - 0.5))

        self._trade_history.append(entry)
        self._trade_history = self._trade_history[-200:]

    def get_adaptive_weights(self, backtest_weights: dict[str, float]) -> dict[str, float]:
        """Blend backtest weights with live-learned scores.

        70% backtest, 30% live learning. Ensures every strategy gets at least 5%.
        """
        MIN_WEIGHT = 0.05

        if not self._strategy_scores:
            return backtest_weights

        scores_positive = {k: max(v, 0.01) for k, v in self._strategy_scores.items()}
        total_score = sum(scores_positive.values())
        learned_weights = {k: v / total_score for k, v in scores_positive.items()} if total_score > 0 else {}

        blended: dict[str, float] = {}
        all_keys = set(backtest_weights) | set(learned_weights)
        for k in all_keys:
            bt = backtest_weights.get(k, 0.25)
            lw = learned_weights.get(k, 0.25)
            blended[k] = 0.7 * bt + 0.3 * lw

        for k in blended:
            blended[k] = max(blended[k], MIN_WEIGHT)

        total = sum(blended.values())
        return {k: v / total for k, v in blended.items()} if total > 0 else backtest_weights

    def get_learning_summary(self) -> dict[str, Any]:
        """Return a summary for the orchestrator prompt."""
        recent = self._trade_history[-20:]
        if not recent:
            return {"total_trades": 0, "message": "No trade history yet — use backtest weights."}

        wins = sum(1 for t in recent if t.get("pnl_pct", 0) > 0)
        total_pnl = sum(t.get("pnl_pct", 0) for t in recent)

        best_strategies: list[tuple[str, float]] = sorted(
            self._strategy_scores.items(), key=lambda x: x[1], reverse=True
        )

        return {
            "total_trades": len(recent),
            "win_rate": wins / len(recent),
            "total_pnl_pct": total_pnl,
            "best_strategy": best_strategies[0][0] if best_strategies else "unknown",
            "strategy_rankings": {k: round(v, 3) for k, v in best_strategies[:5]},
        }

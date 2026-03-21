"""Tests for the adaptive learning engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
from unittest.mock import AsyncMock

import pytest

from engine.learner import AdaptiveLearner


class TestRecordTrade:
    def test_record_winning_trade_boosts_strategy(self):
        learner = AdaptiveLearner()
        learner.record_trade(
            pair="BTC/USD",
            side="BUY",
            pnl_pct=2.5,
            strategy_signals={
                "momentum_breakout": {"signal": "buy", "score": 0.6, "confidence": 0.8},
                "mean_reversion": {"signal": "neutral", "score": 0.0, "confidence": 0.3},
            },
            confidence=0.7,
        )
        scores = learner.strategy_scores
        assert scores["momentum_breakout"] > 0.5
        assert scores["mean_reversion"] < 0.5

    def test_record_losing_trade_penalizes_strategy(self):
        learner = AdaptiveLearner()
        learner.record_trade(
            pair="ETH/USD",
            side="BUY",
            pnl_pct=-3.0,
            strategy_signals={
                "scalp_micro": {"signal": "buy", "score": 0.5, "confidence": 0.6},
            },
            confidence=0.6,
        )
        scores = learner.strategy_scores
        assert scores["scalp_micro"] < 0.5

    def test_multiple_trades_accumulate(self):
        learner = AdaptiveLearner()
        for _ in range(5):
            learner.record_trade(
                pair="BTC/USD", side="BUY", pnl_pct=1.0,
                strategy_signals={"trend_rider": {"signal": "buy", "score": 0.5}},
                confidence=0.7,
            )
        for _ in range(5):
            learner.record_trade(
                pair="ETH/USD", side="BUY", pnl_pct=-1.0,
                strategy_signals={"scalp_micro": {"signal": "buy", "score": 0.5}},
                confidence=0.5,
            )
        assert learner.strategy_scores["trend_rider"] > learner.strategy_scores["scalp_micro"]

    def test_history_capped_at_200(self):
        learner = AdaptiveLearner()
        for i in range(250):
            learner.record_trade(
                pair="BTC/USD", side="BUY", pnl_pct=0.1,
                strategy_signals={"a": {"signal": "buy", "score": 0.5}},
                confidence=0.5,
            )
        assert len(learner._trade_history) <= 200


class TestGetAdaptiveWeights:
    def test_no_live_data_returns_backtest_weights(self):
        learner = AdaptiveLearner()
        bt_weights = {"a": 0.5, "b": 0.3, "c": 0.2}
        result = learner.get_adaptive_weights(bt_weights)
        assert result == bt_weights

    def test_blends_backtest_and_live(self):
        learner = AdaptiveLearner()
        for _ in range(10):
            learner.record_trade(
                pair="BTC/USD", side="BUY", pnl_pct=2.0,
                strategy_signals={"a": {"signal": "buy", "score": 0.7}},
                confidence=0.8,
            )
        bt_weights = {"a": 0.25, "b": 0.75}
        result = learner.get_adaptive_weights(bt_weights)
        assert result["a"] > 0.25

    def test_minimum_weight_enforced(self):
        learner = AdaptiveLearner()
        learner._strategy_scores = {"a": 100.0, "b": 0.001}
        bt_weights = {"a": 0.95, "b": 0.05}
        result = learner.get_adaptive_weights(bt_weights)
        assert result["b"] >= 0.04

    def test_weights_sum_to_one(self):
        learner = AdaptiveLearner()
        learner._strategy_scores = {"x": 2.0, "y": 1.0, "z": 0.5}
        bt_weights = {"x": 0.5, "y": 0.3, "z": 0.2}
        result = learner.get_adaptive_weights(bt_weights)
        assert abs(sum(result.values()) - 1.0) < 0.001


class TestGetLearningSummary:
    def test_empty_history(self):
        learner = AdaptiveLearner()
        summary = learner.get_learning_summary()
        assert summary["total_trades"] == 0
        assert "message" in summary

    def test_with_trades(self):
        learner = AdaptiveLearner()
        for i in range(5):
            learner.record_trade(
                pair="BTC/USD", side="BUY", pnl_pct=1.0 if i < 3 else -0.5,
                strategy_signals={"trend_rider": {"signal": "buy", "score": 0.5}},
                confidence=0.6,
            )
        summary = learner.get_learning_summary()
        assert summary["total_trades"] == 5
        assert 0 < summary["win_rate"] <= 1.0
        assert "best_strategy" in summary
        assert "strategy_rankings" in summary


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load(self):
        mock_redis = AsyncMock()
        stored = {}

        async def mock_set(key, value, ex=None):
            stored[key] = value

        async def mock_get(key):
            return stored.get(key)

        mock_redis.set = mock_set
        mock_redis.get = mock_get

        learner1 = AdaptiveLearner()
        learner1.record_trade(
            pair="BTC/USD", side="BUY", pnl_pct=2.0,
            strategy_signals={"a": {"signal": "buy", "score": 0.6}},
            confidence=0.7,
        )
        await learner1.save(mock_redis)

        learner2 = AdaptiveLearner()
        await learner2.load(mock_redis)

        assert len(learner2._trade_history) == 1
        assert "a" in learner2.strategy_scores
        assert learner2.strategy_scores["a"] == pytest.approx(learner1.strategy_scores["a"])

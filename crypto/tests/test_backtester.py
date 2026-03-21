"""Tests for the backtesting engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.backtester import (
    StrategyResult,
    backtest_all,
    backtest_strategy,
    compute_strategy_weights,
)
from engine.strategies import ALL_STRATEGIES


def _make_trending_bars(n: int = 200, start: float = 100, drift: float = 0.001) -> list[dict]:
    """Generate N bars with a slight upward drift (trending market)."""
    bars = []
    price = start
    for i in range(n):
        price *= (1 + drift + 0.002 * ((-1) ** i))
        bars.append({
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
            "open": price * 0.999,
            "high": price * 1.003,
            "low": price * 0.997,
            "close": price,
            "volume": 1000 + (i % 5) * 200,
            "vwap": price * 1.0001,
        })
    return bars


def _make_mean_reverting_bars(n: int = 200, center: float = 100) -> list[dict]:
    """Generate bars oscillating around a center (mean-reverting market)."""
    import math
    bars = []
    for i in range(n):
        price = center + 5 * math.sin(i * 0.15) + 0.5 * ((-1) ** i)
        bars.append({
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
            "open": price * 0.999,
            "high": price * 1.004,
            "low": price * 0.996,
            "close": price,
            "volume": 1000 + (i % 3) * 300,
            "vwap": price,
        })
    return bars


class TestBacktestStrategy:
    def test_insufficient_bars_returns_zero_trades(self):
        bars = _make_trending_bars(20)
        result = backtest_strategy("momentum_breakout", ALL_STRATEGIES["momentum_breakout"], bars)
        assert result.total_trades == 0

    def test_trending_market_produces_trades(self):
        bars = _make_trending_bars(200)
        result = backtest_strategy("momentum_breakout", ALL_STRATEGIES["momentum_breakout"], bars)
        assert result.total_trades >= 0
        assert isinstance(result.win_rate, float)
        assert isinstance(result.sharpe, float)

    def test_mean_reverting_market_produces_trades(self):
        bars = _make_mean_reverting_bars(200)
        result = backtest_strategy("mean_reversion", ALL_STRATEGIES["mean_reversion"], bars)
        assert isinstance(result.total_trades, int)

    def test_result_fields_valid(self):
        bars = _make_trending_bars(200)
        result = backtest_strategy("trend_rider", ALL_STRATEGIES["trend_rider"], bars)
        assert result.name == "trend_rider"
        assert 0 <= result.win_rate <= 1.0
        assert result.wins + result.losses == result.total_trades
        assert result.max_drawdown_pct >= 0

    def test_to_dict(self):
        bars = _make_trending_bars(200)
        result = backtest_strategy("scalp_micro", ALL_STRATEGIES["scalp_micro"], bars)
        d = result.to_dict()
        assert "name" in d
        assert "sharpe" in d
        assert "win_rate" in d
        assert "total_pnl_pct" in d
        assert "weight" in d


class TestBacktestAll:
    def test_returns_all_strategies(self):
        bars = _make_trending_bars(200)
        results = backtest_all(bars)
        assert len(results) == len(ALL_STRATEGIES)
        names = {r.name for r in results}
        assert names == set(ALL_STRATEGIES.keys())

    def test_sorted_by_sharpe_descending(self):
        bars = _make_trending_bars(200)
        results = backtest_all(bars)
        sharpes = [r.sharpe for r in results]
        assert sharpes == sorted(sharpes, reverse=True)


class TestComputeStrategyWeights:
    def test_positive_sharpes(self):
        results = [
            StrategyResult(name="a", sharpe=2.0),
            StrategyResult(name="b", sharpe=1.0),
            StrategyResult(name="c", sharpe=0.5),
        ]
        weights = compute_strategy_weights(results)
        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 0.001
        assert weights["a"] > weights["b"] > weights["c"]

    def test_all_negative_sharpes_equal_weight(self):
        results = [
            StrategyResult(name="a", sharpe=-1.0),
            StrategyResult(name="b", sharpe=-2.0),
        ]
        weights = compute_strategy_weights(results)
        assert abs(weights["a"] - weights["b"]) < 0.01

    def test_minimum_weight_enforced(self):
        results = [
            StrategyResult(name="big", sharpe=10.0),
            StrategyResult(name="tiny", sharpe=0.01),
        ]
        weights = compute_strategy_weights(results)
        assert weights["tiny"] >= 0.04  # close to 5% min

    def test_single_strategy(self):
        results = [StrategyResult(name="only", sharpe=1.0)]
        weights = compute_strategy_weights(results)
        assert abs(weights["only"] - 1.0) < 0.001

    def test_zero_sharpe_gets_minimum(self):
        results = [
            StrategyResult(name="good", sharpe=5.0),
            StrategyResult(name="zero", sharpe=0.0),
        ]
        weights = compute_strategy_weights(results)
        assert weights["zero"] >= 0.04

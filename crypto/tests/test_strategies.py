"""Tests for aggressive trading strategies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.strategies import (
    ALL_STRATEGIES,
    mean_reversion,
    momentum_breakout,
    run_all_strategies,
    scalp_micro,
    trend_rider,
)


def _make_bars(prices: list[float], base_vol: float = 1000) -> list[dict]:
    """Generate synthetic OHLCV bars from a list of close prices."""
    bars = []
    for i, p in enumerate(prices):
        bars.append({
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
            "open": p * 0.999,
            "high": p * 1.002,
            "low": p * 0.998,
            "close": p,
            "volume": base_vol * (1 + (i % 3) * 0.5),
            "vwap": p * 1.0005,
        })
    return bars


def _make_indicators(**overrides) -> dict:
    defaults = {
        "rsi": 50, "macd_hist": 0.01, "close": 100,
        "bb_upper": 110, "bb_lower": 90, "bb_middle": 100,
        "vwap": 100, "volume": 1000, "volume_sma": 800,
        "williams_r": -50, "ema_9": 101, "ema_21": 100,
        "momentum_5": 0.01, "momentum_10": 0.015,
        "atr": 2.0,
    }
    defaults.update(overrides)
    return defaults


class TestMomentumBreakout:
    def test_insufficient_bars_returns_neutral(self):
        bars = _make_bars([100] * 5)
        sig = momentum_breakout(bars, _make_indicators())
        assert sig["signal"] == "neutral"
        assert sig["name"] == "momentum_breakout"

    def test_breakout_above_high_is_buy(self):
        prices = [100] * 20 + [105]
        bars = _make_bars(prices)
        sig = momentum_breakout(bars, _make_indicators(ema_9=105, ema_21=100))
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.3
        assert sig["confidence"] > 0.3

    def test_breakdown_below_low_is_sell(self):
        prices = [100] * 20 + [94]
        bars = _make_bars(prices)
        sig = momentum_breakout(bars, _make_indicators(ema_9=94, ema_21=100))
        assert sig["signal"] == "sell"
        assert sig["score"] < -0.3

    def test_consolidation_is_neutral(self):
        prices = [100] * 20
        bars = _make_bars(prices)
        sig = momentum_breakout(bars, _make_indicators())
        assert sig["signal"] == "neutral"


class TestMeanReversion:
    def test_insufficient_bars_returns_neutral(self):
        bars = _make_bars([100] * 10)
        sig = mean_reversion(bars, _make_indicators())
        assert sig["signal"] == "neutral"

    def test_oversold_extreme_is_buy(self):
        prices = [100] * 20 + [90]
        bars = _make_bars(prices)
        sig = mean_reversion(bars, _make_indicators(rsi=25, williams_r=-90, close=90))
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.4

    def test_overbought_extreme_is_sell(self):
        prices = [100] * 20 + [112]
        bars = _make_bars(prices)
        sig = mean_reversion(bars, _make_indicators(rsi=75, close=112))
        assert sig["signal"] == "sell"
        assert sig["score"] < -0.3

    def test_near_mean_is_neutral(self):
        prices = [100] * 25
        bars = _make_bars(prices)
        sig = mean_reversion(bars, _make_indicators())
        assert abs(sig["score"]) < 0.3


class TestScalpMicro:
    def test_insufficient_bars_neutral(self):
        bars = _make_bars([100] * 5)
        sig = scalp_micro(bars, _make_indicators())
        assert sig["signal"] == "neutral"

    def test_pullback_in_uptrend_is_buy(self):
        prices = [95, 96, 97, 98, 99, 100, 101, 100.5, 100, 99.5]
        bars = _make_bars(prices)
        sig = scalp_micro(bars, _make_indicators(macd_hist=0.05, vwap=100))
        assert sig["signal"] == "buy"
        assert sig["score"] > 0

    def test_rally_in_downtrend_is_sell(self):
        prices = [105, 104, 103, 102, 101, 100, 99, 99.5, 100, 100.5]
        bars = _make_bars(prices)
        sig = scalp_micro(bars, _make_indicators())
        assert sig["signal"] == "sell"
        assert sig["score"] < 0


class TestTrendRider:
    def test_insufficient_bars_neutral(self):
        bars = _make_bars([100] * 10)
        sig = trend_rider(bars, _make_indicators())
        assert sig["signal"] == "neutral"

    def test_strong_uptrend_is_buy(self):
        prices = list(range(95, 125))
        bars = _make_bars(prices)
        sig = trend_rider(bars, _make_indicators(
            ema_9=123, ema_21=118, momentum_5=0.03, momentum_10=0.05, macd_hist=0.5
        ))
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.5
        assert sig["confidence"] > 0.5

    def test_strong_downtrend_is_sell(self):
        prices = list(range(125, 95, -1))
        bars = _make_bars(prices)
        sig = trend_rider(bars, _make_indicators(
            ema_9=97, ema_21=105, momentum_5=-0.03, momentum_10=-0.05, macd_hist=-0.5
        ))
        assert sig["signal"] == "sell"
        assert sig["score"] < -0.5


class TestRunAllStrategies:
    def test_returns_all_four(self):
        prices = list(range(90, 130))
        bars = _make_bars(prices)
        results = run_all_strategies(bars, _make_indicators())
        assert len(results) == 4
        names = {r["name"] for r in results}
        assert names == {"momentum_breakout", "mean_reversion", "scalp_micro", "trend_rider"}

    def test_all_have_required_keys(self):
        bars = _make_bars([100] * 40)
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert "signal" in r
            assert "score" in r
            assert "confidence" in r
            assert "name" in r

    def test_scores_bounded(self):
        bars = _make_bars(list(range(80, 130)))
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert -1.0 <= r["score"] <= 1.0
            assert 0 <= r["confidence"] <= 1.0


class TestAllStrategiesRegistry:
    def test_registry_has_four_entries(self):
        assert len(ALL_STRATEGIES) == 4

    def test_all_callables(self):
        for name, fn in ALL_STRATEGIES.items():
            assert callable(fn), f"{name} is not callable"

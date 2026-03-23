"""Tests for the AdaptiveMomentumStrategy composite scoring engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.strategies import (
    AdaptiveMomentumStrategy,
    ScoreBreakdown,
    run_all_strategies,
)


def _make_bars(prices: list[float], base_vol: float = 1000) -> list[dict]:
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
        "rsi": 50, "rsi_5": 55, "macd_hist": 0.01,
        "macd_4h_line": 0.5, "macd_4h_signal": 0.3, "macd_4h_hist": 0.2,
        "macd_4h_bullish_cross": False, "macd_4h_bearish_cross": False,
        "close": 100, "high": 101, "low": 99,
        "bb_upper": 110, "bb_lower": 90, "bb_middle": 100,
        "vwap": 98, "volume": 1200, "volume_sma": 800,
        "vol_ratio_20": 1.5,
        "ema_8": 101.5, "ema_9": 101, "ema_13": 100.5,
        "ema_21": 100, "ema_34": 99.5, "ema_55": 99,
        "sma_200": 95,
        "atr": 2.0, "atr_sma_20": 1.5,
    }
    defaults.update(overrides)
    return defaults


def _make_daily_indicators(**overrides) -> dict:
    defaults = {
        "macd_line": 1.0, "macd_signal": 0.5, "macd_hist": 0.5,
        "close": 100,
    }
    defaults.update(overrides)
    return defaults


class TestTechnicalScore:
    def test_all_bullish_conditions(self):
        strategy = AdaptiveMomentumStrategy()
        score, conds = strategy.compute_technical_score(
            _make_indicators(
                macd_4h_line=0.5, macd_4h_signal=0.3,
                rsi_5=65, ema_8=102, ema_21=100,
                close=100, vwap=98, vol_ratio_20=1.5,
            ),
            _make_daily_indicators(),
        )
        assert score >= 60
        assert conds["macd_bullish"] is True
        assert conds["rsi_momentum"] is True
        assert conds["ema_cross"] is True
        assert conds["above_vwap"] is True
        assert conds["volume_confirm"] is True
        assert conds["daily_trend_up"] is True

    def test_all_bearish_conditions(self):
        strategy = AdaptiveMomentumStrategy()
        score, conds = strategy.compute_technical_score(
            _make_indicators(
                macd_4h_line=-0.5, macd_4h_signal=0.3,
                rsi_5=30, ema_8=98, ema_21=100,
                close=95, vwap=98, vol_ratio_20=0.8,
            ),
            _make_daily_indicators(macd_line=-0.5, macd_signal=0.3),
        )
        assert score <= 10
        assert conds["macd_bullish"] is False
        assert conds["rsi_momentum"] is False
        assert conds["ema_cross"] is False

    def test_empty_indicators_returns_zero(self):
        strategy = AdaptiveMomentumStrategy()
        score, conds = strategy.compute_technical_score({})
        assert score == 0.0
        assert conds == {}


class TestSentimentScore:
    def test_extreme_fear_is_bullish(self):
        strategy = AdaptiveMomentumStrategy()
        score = strategy.compute_sentiment_score(
            news_data={"overall_score": 0.3},
            onchain_data={"fear_greed_index": 15, "btc_funding_rate": -0.0005},
        )
        assert score > 30

    def test_extreme_greed_is_bearish(self):
        strategy = AdaptiveMomentumStrategy()
        score = strategy.compute_sentiment_score(
            news_data={"overall_score": -0.2},
            onchain_data={"fear_greed_index": 85, "btc_funding_rate": 0.001},
        )
        assert score < -30

    def test_neutral_sentiment(self):
        strategy = AdaptiveMomentumStrategy()
        score = strategy.compute_sentiment_score(
            news_data={"overall_score": 0.0},
            onchain_data={"fear_greed_index": 50, "btc_funding_rate": 0.0},
        )
        assert -20 < score < 20


class TestOnchainScore:
    def test_short_squeeze_setup(self):
        strategy = AdaptiveMomentumStrategy()
        score = strategy.compute_onchain_score(
            onchain_data={
                "exchange_flow_signal": "outflow",
                "oi_rising": True,
                "btc_funding_rate": -0.0005,
            },
            microstructure={"imbalance": 0.4},
        )
        assert score > 40

    def test_bearish_microstructure(self):
        strategy = AdaptiveMomentumStrategy()
        score = strategy.compute_onchain_score(
            onchain_data={
                "exchange_flow_signal": "inflow",
                "oi_rising": True,
                "btc_funding_rate": 0.002,
                "liquidation_cascade": True,
            },
            microstructure={"imbalance": -0.6},
        )
        assert score < -40


class TestCompositeScore:
    def test_weighted_sum(self):
        strategy = AdaptiveMomentumStrategy()
        result = strategy.composite_score(80, 50, 30)
        expected = 80 * 0.5 + 50 * 0.3 + 30 * 0.2
        assert abs(result - expected) < 0.01

    def test_clamped_to_100(self):
        strategy = AdaptiveMomentumStrategy()
        result = strategy.composite_score(100, 100, 100)
        assert result == 100.0

    def test_clamped_to_neg_100(self):
        strategy = AdaptiveMomentumStrategy()
        result = strategy.composite_score(-100, -100, -100)
        assert result == -100.0


class TestEvaluate:
    def test_full_evaluation(self):
        strategy = AdaptiveMomentumStrategy()
        bd = strategy.evaluate(
            indicators_4h=_make_indicators(),
            indicators_daily=_make_daily_indicators(),
            news_data={"overall_score": 0.5},
            onchain_data={"fear_greed_index": 30, "btc_funding_rate": -0.0003},
            microstructure={"imbalance": 0.3},
        )
        assert isinstance(bd, ScoreBreakdown)
        assert -100 <= bd.composite <= 100
        assert bd.tech_conditions is not None
        assert len(bd.reasons) > 0

    def test_buy_threshold(self):
        strategy = AdaptiveMomentumStrategy()
        bd = strategy.evaluate(
            indicators_4h=_make_indicators(
                macd_4h_line=1.0, macd_4h_signal=0.3,
                rsi_5=72, ema_8=105, ema_21=100,
                close=103, vwap=98, vol_ratio_20=2.0,
            ),
            indicators_daily=_make_daily_indicators(macd_line=2.0, macd_signal=0.5),
            buy_threshold=40,
        )
        assert bd.composite >= 40


class TestRunAllStrategies:
    def test_returns_single_strategy(self):
        bars = _make_bars(list(range(90, 160)))
        results = run_all_strategies(bars, _make_indicators())
        assert len(results) == 1
        assert results[0]["name"] == "adaptive_momentum"

    def test_all_have_required_keys(self):
        bars = _make_bars([100] * 60)
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert "signal" in r
            assert "score" in r
            assert "confidence" in r
            assert "name" in r

    def test_scores_bounded(self):
        bars = _make_bars(list(range(80, 150)))
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert -1.0 <= r["score"] <= 1.0
            assert 0 <= r["confidence"] <= 1.0

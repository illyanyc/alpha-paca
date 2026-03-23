"""Tests for dynamic signal combiner."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.signals import (
    AccuracyTracker,
    ComponentSignal,
    SignalStrength,
    classify_technical,
    composite_score,
    dynamic_composite,
)


class TestClassifyTechnical:
    def test_bullish_setup_returns_buy(self):
        sig = classify_technical({
            "rsi_5": 65,
            "macd_4h_line": 0.5, "macd_4h_signal": 0.3,
            "ema_8": 102, "ema_21": 100,
            "close": 100, "vwap": 98, "vol_ratio_20": 1.3,
        })
        assert sig.signal in (SignalStrength.BUY, SignalStrength.STRONG_BUY)
        assert sig.score > 0

    def test_bearish_setup_returns_sell(self):
        sig = classify_technical({
            "rsi_5": 30,
            "macd_4h_line": -0.5, "macd_4h_signal": 0.3,
            "ema_8": 98, "ema_21": 100,
            "close": 95, "vwap": 100, "vol_ratio_20": 0.7,
        })
        assert sig.signal in (SignalStrength.SELL, SignalStrength.STRONG_SELL)
        assert sig.score < 0

    def test_neutral_indicators(self):
        sig = classify_technical({
            "rsi_5": 50,
            "macd_4h_line": 0.01, "macd_4h_signal": 0.01,
            "ema_8": 100, "ema_21": 100,
            "close": 100, "vwap": 100, "vol_ratio_20": 1.0,
        })
        assert abs(sig.score) < 0.5


class TestCompositeScore:
    def test_all_bullish(self):
        signals = [
            ComponentSignal("technical", SignalStrength.BUY, 0.5, 0.8),
            ComponentSignal("news", SignalStrength.BUY, 0.4, 0.7),
            ComponentSignal("fundamental", SignalStrength.BUY, 0.3, 0.6),
        ]
        score, conf = composite_score(signals)
        assert score > 0
        assert conf > 0

    def test_all_bearish(self):
        signals = [
            ComponentSignal("technical", SignalStrength.SELL, -0.5, 0.8),
            ComponentSignal("news", SignalStrength.SELL, -0.4, 0.7),
        ]
        score, conf = composite_score(signals)
        assert score < 0

    def test_empty_signals(self):
        score, conf = composite_score([])
        assert score == 0
        assert conf == 0


class TestDynamicComposite:
    def test_basic_bullish(self):
        signals = {
            "technical": {"score": 0.5, "confidence": 0.8},
            "news": {"score": 0.3, "confidence": 0.6},
        }
        result = dynamic_composite(signals)
        assert result["score"] > 0
        assert "action" in result
        assert "weights" in result
        assert "composite_100" in result

    def test_conflict_detection(self):
        signals = {
            "technical": {"score": 0.8, "confidence": 0.9},
            "news": {"score": -0.8, "confidence": 0.9},
        }
        result = dynamic_composite(signals)
        assert result["has_conflict"] is True

    def test_regime_modulation(self):
        signals = {
            "technical": {"score": 0.5, "confidence": 0.7},
        }
        trending = dynamic_composite(signals, regime="trending_up")
        neutral = dynamic_composite(signals, regime=None)
        assert trending["score"] != 0 or neutral["score"] != 0

    def test_buy_action_above_threshold(self):
        signals = {
            "technical": {"score": 0.8, "confidence": 0.9},
            "news": {"score": 0.6, "confidence": 0.8},
            "onchain": {"score": 0.4, "confidence": 0.7},
        }
        result = dynamic_composite(signals)
        assert result["composite_100"] > 0


class TestAccuracyTracker:
    def test_empty_accuracy_is_half(self):
        tracker = AccuracyTracker()
        assert tracker.accuracy("technical") == 0.5

    def test_all_correct(self):
        tracker = AccuracyTracker()
        for _ in range(10):
            tracker.record("technical", 1, 1)
        assert tracker.accuracy("technical") == 1.0

    def test_all_wrong(self):
        tracker = AccuracyTracker()
        for _ in range(10):
            tracker.record("technical", 1, -1)
        assert tracker.accuracy("technical") == 0.0

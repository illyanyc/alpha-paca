"""Tests for signal classification and composite scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.signals import (
    SignalStrength,
    ComponentSignal,
    classify_technical,
    composite_score,
)


class TestClassifyTechnical:
    def test_rsi_above_50_bullish(self):
        """RSI(5) > 50 is the Adaptive Momentum buy signal (not RSI < 30)."""
        inds = {
            "rsi_5": 65,
            "macd_4h_line": 0.5, "macd_4h_signal": 0.3,
            "ema_8": 102, "ema_21": 100,
            "close": 100, "vwap": 98,
            "vol_ratio_20": 1.5,
        }
        sig = classify_technical(inds)
        assert sig.score > 0
        assert sig.signal in (SignalStrength.BUY, SignalStrength.STRONG_BUY)

    def test_bearish_setup(self):
        inds = {
            "rsi_5": 30,
            "macd_4h_line": -0.5, "macd_4h_signal": 0.3,
            "ema_8": 98, "ema_21": 100,
            "close": 95, "vwap": 100,
            "vol_ratio_20": 0.8,
        }
        sig = classify_technical(inds)
        assert sig.score < 0

    def test_neutral_indicators(self):
        inds = {
            "rsi_5": 50,
            "macd_4h_line": 0.01, "macd_4h_signal": 0.01,
            "ema_8": 100, "ema_21": 100,
            "close": 100, "vwap": 100,
            "vol_ratio_20": 1.0,
        }
        sig = classify_technical(inds)
        assert -0.4 < sig.score < 0.4

    def test_none_indicators_handled(self):
        inds = {
            "rsi_5": None, "rsi": None,
            "macd_4h_line": None, "macd_4h_signal": None,
            "macd_hist": None,
            "ema_8": None, "ema_21": None,
            "close": None, "vwap": None,
            "vol_ratio_20": None,
        }
        sig = classify_technical(inds)
        assert sig.signal == SignalStrength.NEUTRAL

    def test_fallback_to_rsi14(self):
        """When rsi_5 is None, falls back to rsi (period 14)."""
        inds = {
            "rsi_5": None, "rsi": 60,
            "macd_4h_line": 0.5, "macd_4h_signal": 0.3,
            "ema_8": 102, "ema_21": 100,
            "close": 100, "vwap": 98,
        }
        sig = classify_technical(inds)
        assert sig.score > 0

    def test_volume_spike_amplifies(self):
        base_inds = {
            "rsi_5": 60,
            "macd_4h_line": 0.5, "macd_4h_signal": 0.3,
            "ema_8": 102, "ema_21": 100,
            "close": 100, "vwap": 98,
        }
        sig_normal = classify_technical({**base_inds, "vol_ratio_20": 1.0})
        sig_spike = classify_technical({**base_inds, "vol_ratio_20": 2.5})
        assert sig_spike.score > sig_normal.score


class TestCompositeScore:
    def test_all_bullish(self):
        signals = [
            ComponentSignal("technical", SignalStrength.STRONG_BUY, 0.9, 0.9),
            ComponentSignal("news", SignalStrength.BUY, 0.5, 0.8),
            ComponentSignal("fundamental", SignalStrength.BUY, 0.4, 0.7),
        ]
        score, conf = composite_score(signals)
        assert score > 0.3
        assert conf > 0.5

    def test_mixed_signals(self):
        signals = [
            ComponentSignal("technical", SignalStrength.STRONG_BUY, 0.9, 0.9),
            ComponentSignal("news", SignalStrength.STRONG_SELL, -0.9, 0.9),
        ]
        score, conf = composite_score(signals)
        assert -0.3 < score < 0.3

    def test_empty_signals(self):
        score, conf = composite_score([])
        assert score == 0.0
        assert conf == 0.0

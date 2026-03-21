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
    def test_oversold_rsi_is_bullish(self):
        inds = {
            "rsi": 25, "macd_hist": 0.1, "close": 100,
            "bb_upper": 110, "bb_lower": 90, "vwap": 95,
            "volume": 1000, "volume_sma": 800,
        }
        sig = classify_technical(inds)
        assert sig.score > 0
        assert sig.signal in (SignalStrength.BUY, SignalStrength.STRONG_BUY)

    def test_overbought_rsi_is_bearish(self):
        inds = {
            "rsi": 80, "macd_hist": -0.1, "close": 115,
            "bb_upper": 110, "bb_lower": 90, "vwap": 105,
            "volume": 500, "volume_sma": 800,
        }
        sig = classify_technical(inds)
        assert sig.score < 0

    def test_neutral_indicators(self):
        inds = {
            "rsi": 50, "macd_hist": 0.001, "close": 100,
            "bb_upper": 110, "bb_lower": 90, "vwap": 100,
            "volume": 800, "volume_sma": 800,
        }
        sig = classify_technical(inds)
        assert -0.3 < sig.score < 0.3

    def test_none_indicators_handled(self):
        inds = {
            "rsi": None, "macd_hist": None, "close": None,
            "bb_upper": None, "bb_lower": None, "vwap": None,
            "volume": None, "volume_sma": None,
        }
        sig = classify_technical(inds)
        assert sig.signal == SignalStrength.NEUTRAL


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

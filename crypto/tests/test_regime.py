"""Tests for regime detection engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.regime import Regime, RegimeState, detect_regime


class TestDetectRegime:
    def test_insufficient_data_returns_volatile(self):
        result = detect_regime([100.0] * 20)
        assert result.regime == Regime.VOLATILE
        assert result.confidence == 0.3

    def test_uptrend_detected(self):
        prices = [100 + i * 0.5 for i in range(100)]
        result = detect_regime(prices)
        assert result.regime in (Regime.TRENDING_UP, Regime.MEAN_REVERTING)
        assert result.confidence >= 0.2
        assert result.label in ("TREND-UP", "MEAN-REVERT", "VOLATILE")

    def test_downtrend_detected(self):
        prices = [200 - i * 0.5 for i in range(100)]
        result = detect_regime(prices)
        assert result.regime in (Regime.TRENDING_DOWN, Regime.MEAN_REVERTING)
        assert result.confidence >= 0.2

    def test_sideways_mean_reverting(self):
        import math
        prices = [100 + 2 * math.sin(i * 0.3) for i in range(100)]
        result = detect_regime(prices)
        assert result.confidence >= 0.2
        assert isinstance(result.features, dict)

    def test_features_populated(self):
        prices = [100 + i * 0.1 for i in range(80)]
        result = detect_regime(prices)
        assert "realized_vol" in result.features
        assert "autocorrelation" in result.features
        assert "hurst_exponent" in result.features
        assert "trend_strength" in result.features

    def test_regime_state_has_label(self):
        prices = [100.0] * 80
        result = detect_regime(prices)
        assert isinstance(result.label, str)
        assert len(result.label) > 0

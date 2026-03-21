"""Tests for position sizing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.position_sizer import fractional_kelly, compute_position_size


class TestFractionalKelly:
    def test_positive_edge(self):
        result = fractional_kelly(0.6, 1.5, 1.0, 0.25)
        assert result > 0

    def test_no_edge(self):
        result = fractional_kelly(0.5, 1.0, 1.0, 0.25)
        assert result == 0.0

    def test_negative_edge(self):
        result = fractional_kelly(0.3, 1.0, 1.0, 0.25)
        assert result == 0.0

    def test_zero_loss(self):
        result = fractional_kelly(0.6, 1.5, 0.0, 0.25)
        assert result == 0.0


class TestComputePositionSize:
    def test_basic_sizing(self):
        ps = compute_position_size(
            pair="BTC/USD", price=70000, confidence=0.8,
            atr_value=500, available_capital=1000, current_exposure_pct=0,
        )
        assert ps.qty > 0
        assert ps.notional_usd > 0
        assert ps.pct_of_capital > 0

    def test_high_exposure_limits_size(self):
        ps = compute_position_size(
            pair="BTC/USD", price=70000, confidence=0.8,
            atr_value=500, available_capital=1000, current_exposure_pct=85,
        )
        assert ps.pct_of_capital <= 10

    def test_zero_price(self):
        ps = compute_position_size(
            pair="BTC/USD", price=0, confidence=0.8,
            atr_value=500, available_capital=1000, current_exposure_pct=0,
        )
        assert ps.qty == 0

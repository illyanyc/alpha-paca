"""Tests for advanced position sizer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.position_sizer import (
    PositionSize,
    TradeResultTracker,
    compute_position_size,
    fractional_kelly,
    get_trade_tracker,
)


class TestFractionalKelly:
    def test_50_50_returns_zero(self):
        result = fractional_kelly(0.5, 1.0, 1.0)
        assert result == 0.0

    def test_high_winrate(self):
        result = fractional_kelly(0.7, 1.5, 1.0)
        assert result > 0
        assert result < 1.0

    def test_zero_loss_returns_zero(self):
        result = fractional_kelly(0.6, 1.0, 0.0)
        assert result == 0.0


class TestTradeResultTracker:
    def test_consecutive_losses(self):
        t = TradeResultTracker()
        t.record("BTC/USD", False)
        t.record("BTC/USD", False)
        t.record("BTC/USD", False)
        assert t.consecutive_losses() == 3

    def test_win_resets_count(self):
        t = TradeResultTracker()
        t.record("BTC/USD", False)
        t.record("BTC/USD", True)
        t.record("BTC/USD", False)
        assert t.consecutive_losses() == 1

    def test_pair_losses(self):
        t = TradeResultTracker()
        t.record("BTC/USD", False)
        t.record("ETH/USD", True)
        t.record("BTC/USD", False)
        assert t.pair_consecutive_losses("BTC/USD") == 2

    def test_pair_win_resets(self):
        t = TradeResultTracker()
        t.record("BTC/USD", False)
        t.record("BTC/USD", True)
        t.record("BTC/USD", False)
        assert t.pair_consecutive_losses("BTC/USD") == 1


class TestComputePositionSize:
    def test_basic_size(self):
        result = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20,
        )
        assert isinstance(result, PositionSize)
        assert result.qty > 0
        assert result.notional_usd > 0
        assert result.pct_of_capital > 0

    def test_high_exposure_limits_size(self):
        small = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=85,
        )
        large = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20,
        )
        assert small.pct_of_capital <= large.pct_of_capital

    def test_volatile_regime_reduces_size(self):
        normal = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20, regime="trending_up",
        )
        volatile = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20, regime="volatile",
        )
        assert volatile.pct_of_capital < normal.pct_of_capital

    def test_zero_price(self):
        result = compute_position_size(
            pair="BTC/USD", price=0, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20,
        )
        assert result.qty == 0

    def test_adjustments_populated(self):
        result = compute_position_size(
            pair="BTC/USD", price=50000, confidence=0.7,
            atr_value=500, available_capital=10000,
            current_exposure_pct=20, regime="volatile",
        )
        assert "volatility" in result.adjustments
        assert "regime" in result.adjustments
        assert "correlation" in result.adjustments
        assert "anti_martingale" in result.adjustments

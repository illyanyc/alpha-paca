"""Tests for the conviction-based leverage sizer."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.leverage_sizer import (
    ConsecutiveLossTracker,
    SizedOrder,
    compute_leverage_size,
    get_loss_tracker,
    _conviction_to_leverage,
    _atr_volatility_scalar,
    _anti_martingale_scalar,
)


class TestConvictionToLeverage:
    def test_below_min_returns_zero(self):
        assert _conviction_to_leverage(0.70) == 0.0

    def test_tier_1(self):
        assert _conviction_to_leverage(0.75) == 1.0
        assert _conviction_to_leverage(0.80) == 1.0
        assert _conviction_to_leverage(0.84) == 1.0

    def test_tier_2(self):
        assert _conviction_to_leverage(0.85) == 2.0
        assert _conviction_to_leverage(0.89) == 2.0

    def test_tier_3(self):
        assert _conviction_to_leverage(0.90) == 3.0
        assert _conviction_to_leverage(0.94) == 3.0

    def test_tier_4(self):
        assert _conviction_to_leverage(0.95) == 5.0
        assert _conviction_to_leverage(1.00) == 5.0


class TestATRVolatilityScalar:
    def test_no_atr_returns_1(self):
        assert _atr_volatility_scalar(None, 1000) == 1.0

    def test_zero_price_returns_1(self):
        assert _atr_volatility_scalar(50, 0) == 1.0

    def test_high_vol_reduces(self):
        assert _atr_volatility_scalar(60, 1000) < 1.0

    def test_low_vol_keeps_1(self):
        assert _atr_volatility_scalar(10, 1000) == 1.0


class TestConsecutiveLossTracker:
    def test_empty_tracker(self):
        t = ConsecutiveLossTracker()
        assert t.consecutive_losses("day") == 0

    def test_tracks_losses(self):
        t = ConsecutiveLossTracker()
        t.record("day", "BTC/USD", False)
        t.record("day", "ETH/USD", False)
        t.record("day", "SOL/USD", False)
        assert t.consecutive_losses("day") == 3

    def test_win_resets(self):
        t = ConsecutiveLossTracker()
        t.record("day", "BTC/USD", False)
        t.record("day", "BTC/USD", False)
        t.record("day", "BTC/USD", True)
        t.record("day", "BTC/USD", False)
        assert t.consecutive_losses("day") == 1

    def test_separate_bots(self):
        t = ConsecutiveLossTracker()
        t.record("day", "BTC/USD", False)
        t.record("day", "BTC/USD", False)
        t.record("swing", "BTC/USD", True)
        assert t.consecutive_losses("day") == 2
        assert t.consecutive_losses("swing") == 0


class TestComputeLeverageSize:
    def test_low_conviction_returns_none(self):
        result = compute_leverage_size("BTC/USD", 0.60, "day", 10000)
        assert result is None

    def test_basic_size_tier_1(self):
        result = compute_leverage_size("BTC/USD", 0.80, "day", 10000)
        assert result is not None
        assert isinstance(result, SizedOrder)
        assert result.effective_leverage == 1.0
        assert result.notional_usd > 0
        assert result.pct_of_capital > 0

    def test_higher_conviction_more_leverage(self):
        r1 = compute_leverage_size("BTC/USD", 0.80, "day", 10000)
        r2 = compute_leverage_size("BTC/USD", 0.92, "day", 10000)
        assert r1 is not None and r2 is not None
        assert r2.effective_leverage > r1.effective_leverage
        assert r2.notional_usd > r1.notional_usd

    def test_atr_reduces_size(self):
        r_normal = compute_leverage_size("BTC/USD", 0.80, "day", 10000, atr_value=500, price=50000)
        r_high_vol = compute_leverage_size("BTC/USD", 0.80, "day", 10000, atr_value=3000, price=50000)
        assert r_normal is not None and r_high_vol is not None
        assert r_high_vol.notional_usd <= r_normal.notional_usd

    def test_adjustments_populated(self):
        result = compute_leverage_size("BTC/USD", 0.80, "day", 10000, atr_value=500, price=50000)
        assert result is not None
        assert "base_leverage" in result.adjustments
        assert "volatility" in result.adjustments
        assert "anti_martingale" in result.adjustments

    def test_zero_capital_returns_minimal(self):
        result = compute_leverage_size("BTC/USD", 0.80, "day", 0)
        assert result is not None
        assert result.notional_usd == 0.0

    def test_max_leverage_cap(self):
        result = compute_leverage_size("BTC/USD", 0.99, "day", 10000)
        assert result is not None
        assert result.effective_leverage <= 5.0

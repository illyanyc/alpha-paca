"""Tests for the ATR-based fixed-fractional position sizer."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.leverage_sizer import (
    ConsecutiveLossTracker,
    SizedOrder,
    compute_position_size,
    compute_leverage_size,
    get_loss_tracker,
    _atr_volatility_scalar,
    _anti_martingale_scalar,
)


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


class TestComputePositionSize:
    def test_basic_atr_sizing(self):
        result = compute_position_size(
            pair="BTC/USD", bot_id="momentum",
            account_nav=100000, entry_price=85000,
            atr_value=3500,
        )
        assert result is not None
        assert isinstance(result, SizedOrder)
        assert result.notional_usd > 0
        assert result.stop_distance > 0

    def test_risk_amount_calculation(self):
        result = compute_position_size(
            pair="BTC/USD", bot_id="momentum",
            account_nav=100000, entry_price=85000,
            atr_value=3500, risk_per_trade_pct=1.5,
        )
        assert result is not None
        assert result.notional_usd > 10

    def test_max_single_position_cap(self):
        result = compute_position_size(
            pair="BTC/USD", bot_id="momentum",
            account_nav=10000, entry_price=85000,
            atr_value=100,
        )
        assert result is not None
        assert result.notional_usd <= 10000 * 0.33

    def test_zero_nav_returns_none(self):
        result = compute_position_size(
            pair="BTC/USD", bot_id="momentum",
            account_nav=0, entry_price=85000,
            atr_value=3500,
        )
        assert result is None

    def test_zero_atr_returns_none(self):
        result = compute_position_size(
            pair="BTC/USD", bot_id="momentum",
            account_nav=100000, entry_price=85000,
            atr_value=0,
        )
        assert result is None


class TestComputeLeverageSize:
    def test_low_conviction_returns_none(self):
        result = compute_leverage_size("BTC/USD", 0.40, "day", 10000)
        assert result is None

    def test_basic_size_with_atr(self):
        result = compute_leverage_size(
            "BTC/USD", 0.80, "momentum", 10000,
            atr_value=3000, price=85000,
        )
        assert result is not None
        assert isinstance(result, SizedOrder)
        assert result.notional_usd > 0

    def test_atr_reduces_size(self):
        r_normal = compute_leverage_size("BTC/USD", 0.80, "momentum", 10000, atr_value=500, price=50000)
        r_high_vol = compute_leverage_size("BTC/USD", 0.80, "momentum", 10000, atr_value=3000, price=50000)
        assert r_normal is not None and r_high_vol is not None
        assert r_high_vol.notional_usd <= r_normal.notional_usd

    def test_fallback_without_atr(self):
        result = compute_leverage_size("BTC/USD", 0.80, "momentum", 10000)
        assert result is not None
        assert result.notional_usd > 0

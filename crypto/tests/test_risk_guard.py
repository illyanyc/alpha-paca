"""Tests for the RiskGuard shared risk engine — Adaptive Momentum edition."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.risk_guard import RiskGuard, RiskVerdict


@pytest.fixture
def guard():
    return RiskGuard()


def _portfolio(**overrides):
    defaults = {
        "nav": 10000,
        "cash": 8000,
        "total_exposure_pct": 20,
        "drawdown_pct": 0,
        "realized_pnl_today": 0,
    }
    defaults.update(overrides)
    return defaults


def _decision(**overrides):
    defaults = {
        "action": "BUY",
        "pair": "BTC/USD",
        "size_pct": 1.0,
        "conviction": 0.85,
        "target_price": 90000,
        "stop_price": 80000,
        "entry_price": 84000,
    }
    defaults.update(overrides)
    return defaults


class TestSellAlwaysApproved:
    def test_sell_bypasses_all_checks(self, guard):
        guard.force_daily_halt()
        v = guard.check("momentum", _decision(action="SELL"), [], _portfolio(drawdown_pct=50))
        assert v.approved is True


class TestDailyHalt:
    def test_daily_halt_blocks_buy(self, guard):
        guard.force_daily_halt()
        v = guard.check("momentum", _decision(), [], _portfolio())
        assert v.approved is False
        assert "Daily loss halt" in v.reason

    def test_reset_daily_halt_allows_trading(self, guard):
        guard.force_daily_halt()
        guard.reset_daily_halt()
        v = guard.check("momentum", _decision(), [], _portfolio())
        assert v.approved is True


class TestDrawdown:
    def test_drawdown_at_limit_blocks(self, guard):
        v = guard.check("swing", _decision(), [], _portfolio(drawdown_pct=10))
        assert v.approved is False
        assert "Drawdown" in v.reason

    def test_drawdown_below_limit_passes(self, guard):
        good_swing = _decision(entry_price=100, target_price=115, stop_price=95)
        v = guard.check("swing", good_swing, [], _portfolio(drawdown_pct=5))
        assert v.approved is True


class TestDailyLoss:
    def test_daily_loss_triggers_halt_at_2pct(self, guard):
        """Daily halt now triggers at 2% (down from 5%)."""
        v = guard.check(
            "momentum", _decision(), [],
            _portfolio(nav=10000, realized_pnl_today=-250),
        )
        assert v.approved is False
        assert "Daily loss" in v.reason
        assert guard._daily_halt is True


class TestExposure:
    def test_full_exposure_blocks(self, guard):
        v = guard.check("momentum", _decision(), [], _portfolio(total_exposure_pct=100))
        assert v.approved is False
        assert "exposure" in v.reason.lower()


class TestConcurrentPositions:
    def test_total_limit_3_reached(self, guard):
        """Max concurrent total is now 3 (down from 5)."""
        positions = [
            {"pair": "BTC/USD", "bot_id": "momentum", "qty": 0.1},
            {"pair": "ETH/USD", "bot_id": "momentum", "qty": 1.0},
            {"pair": "SOL/USD", "bot_id": "swing", "qty": 10.0},
        ]
        v = guard.check("momentum", _decision(pair="XRP/USD"), positions, _portfolio())
        assert v.approved is False
        assert "positions" in v.reason.lower() or "Total" in v.reason

    def test_under_limits_passes(self, guard):
        positions = [{"pair": "BTC/USD", "bot_id": "momentum", "qty": 0.1}]
        v = guard.check("momentum", _decision(pair="ETH/USD"), positions, _portfolio())
        assert v.approved is True


class TestPerTradeRisk:
    def test_oversized_trade_blocked(self, guard):
        """Max risk per trade is now 1.5%."""
        v = guard.check("momentum", _decision(size_pct=2.0), [], _portfolio())
        assert v.approved is False
        assert "Trade size" in v.reason

    def test_normal_size_passes(self, guard):
        v = guard.check("momentum", _decision(size_pct=1.0), [], _portfolio())
        assert v.approved is True


class TestRRRatio:
    def test_bad_rr_for_swing_blocked(self, guard):
        v = guard.check(
            "swing",
            _decision(entry_price=100, target_price=101, stop_price=99),
            [], _portfolio(),
        )
        assert v.approved is False
        assert "R/R" in v.reason

    def test_good_rr_for_swing_passes(self, guard):
        v = guard.check(
            "swing",
            _decision(entry_price=100, target_price=110, stop_price=97),
            [], _portfolio(),
        )
        assert v.approved is True


class TestAntiChurn:
    def test_immediate_re_trade_blocked(self, guard):
        guard.record_trade_time("momentum", "BTC/USD")
        v = guard.check("momentum", _decision(pair="BTC/USD"), [], _portfolio())
        assert v.approved is False
        assert "traded" in v.reason.lower()

    def test_different_pair_allowed(self, guard):
        guard.record_trade_time("momentum", "BTC/USD")
        v = guard.check("momentum", _decision(pair="ETH/USD"), [], _portfolio())
        assert v.approved is True


class TestConsecutiveLossCooldown:
    def test_five_losses_halts_bot(self, guard):
        for _ in range(5):
            guard.record_loss("momentum")
        v = guard.check("momentum", _decision(), [], _portfolio())
        assert v.approved is False
        assert "halted" in v.reason.lower() or "consecutive" in v.reason.lower()

    def test_win_resets_losses(self, guard):
        for _ in range(4):
            guard.record_loss("momentum")
        guard.record_win("momentum")
        v = guard.check("momentum", _decision(), [], _portfolio())
        assert v.approved is True


class TestTradingHours:
    def test_outside_hours_blocked(self, guard):
        with patch("agents.risk_guard.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v = guard._check_trading_hours()
            assert v.approved is False

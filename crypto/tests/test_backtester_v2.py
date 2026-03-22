"""Tests for the walk-forward backtester v2 (DaySniper + SwingSniper replay)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.backtester_v2 import (
    BacktestMetrics,
    SimTrade,
    _apply_cost,
    metrics_to_dict,
    replay_day_bot,
    replay_swing_bot,
)


def _make_5m_candles(n: int = 200, start_price: float = 80000, drift: float = 0.0002):
    """Generate n synthetic 5-minute candles with slight upward drift."""
    candles = []
    price = start_price
    for i in range(n):
        price *= (1 + drift + 0.001 * ((-1) ** i))
        candles.append({
            "timestamp": f"2026-01-01T{(i * 5) // 60:02d}:{(i * 5) % 60:02d}:00Z",
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 100 + (i % 5) * 20,
        })
    return candles


def _make_4h_candles(n: int = 100, start_price: float = 80000, drift: float = 0.001):
    """Generate n synthetic 4-hour candles."""
    candles = []
    price = start_price
    for i in range(n):
        price *= (1 + drift + 0.003 * ((-1) ** i))
        candles.append({
            "timestamp": f"2026-01-{1 + (i * 4) // 24:02d}T{(i * 4) % 24:02d}:00:00Z",
            "open": price * 0.998,
            "high": price * 1.005,
            "low": price * 0.995,
            "close": price,
            "volume": 500 + (i % 3) * 100,
        })
    return candles


class TestApplyCost:
    def test_buy_increases_price(self):
        assert _apply_cost(100, "BUY") > 100

    def test_sell_decreases_price(self):
        assert _apply_cost(100, "SELL") < 100

    def test_symmetry(self):
        buy = _apply_cost(1000, "BUY")
        sell = _apply_cost(1000, "SELL")
        assert buy > sell


class TestReplayDayBot:
    @pytest.mark.asyncio
    async def test_insufficient_candles(self):
        async def mock_agent(ind, candles, regime, port):
            return []

        m = await replay_day_bot([], mock_agent)
        assert m.total_trades == 0
        assert m.bot_id == "day"

    @pytest.mark.asyncio
    async def test_hold_only_agent(self):
        async def mock_agent(ind, candles, regime, port):
            return [{"action": "HOLD", "pair": "BTC/USD"}]

        candles = _make_5m_candles(200)
        m = await replay_day_bot(candles, mock_agent, sample_every=10)
        assert m.total_trades == 0
        assert m.total_return_pct == pytest.approx(0, abs=0.1)

    @pytest.mark.asyncio
    async def test_buy_then_sell_agent(self):
        call_count = 0

        async def mock_agent(ind, candles, regime, port):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"action": "BUY", "pair": "BTC/USD", "target_price": 999999, "stop_price": 0}]
            if call_count == 5:
                return [{"action": "SELL", "pair": "BTC/USD"}]
            return [{"action": "HOLD", "pair": "BTC/USD"}]

        candles = _make_5m_candles(200)
        m = await replay_day_bot(candles, mock_agent, sample_every=5)
        assert m.total_trades >= 1

    @pytest.mark.asyncio
    async def test_stop_loss_triggers(self):
        async def buy_agent(ind, candles, regime, port):
            price = candles[-1]["close"] if candles else 100
            return [{"action": "BUY", "pair": "BTC/USD", "target_price": price * 2, "stop_price": price * 0.999}]

        candles = _make_5m_candles(200, start_price=100)
        m = await replay_day_bot(candles, buy_agent, sample_every=5)
        assert m.total_trades >= 1

    @pytest.mark.asyncio
    async def test_metrics_structure(self):
        async def mock_agent(ind, candles, regime, port):
            return [{"action": "HOLD"}]

        m = await replay_day_bot(_make_5m_candles(100), mock_agent, sample_every=20)
        assert isinstance(m, BacktestMetrics)
        assert m.bot_id == "day"
        assert isinstance(m.total_return_pct, float)
        assert isinstance(m.sharpe_ratio, float)
        assert isinstance(m.win_rate, float)
        assert isinstance(m.max_drawdown_pct, float)


class TestReplaySwingBot:
    @pytest.mark.asyncio
    async def test_insufficient_candles(self):
        async def mock_agent(ind, candles, regime, port):
            return []

        m = await replay_swing_bot([], mock_agent)
        assert m.total_trades == 0
        assert m.bot_id == "swing"

    @pytest.mark.asyncio
    async def test_hold_only(self):
        async def mock_agent(ind, candles, regime, port):
            return [{"action": "HOLD"}]

        candles = _make_4h_candles(60)
        m = await replay_swing_bot(candles, mock_agent)
        assert m.total_trades == 0

    @pytest.mark.asyncio
    async def test_buy_and_sell(self):
        call_count = 0

        async def mock_agent(ind, candles, regime, port):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"action": "BUY", "pair": "BTC/USD", "target_price": 999999, "stop_price": 0}]
            if call_count == 10:
                return [{"action": "SELL", "pair": "BTC/USD"}]
            return [{"action": "HOLD"}]

        candles = _make_4h_candles(80)
        m = await replay_swing_bot(candles, mock_agent)
        assert m.total_trades >= 1


class TestMetricsToDict:
    def test_basic_serialization(self):
        m = BacktestMetrics(
            bot_id="day",
            total_return_pct=5.123,
            sharpe_ratio=1.456,
            win_rate=0.6667,
            total_trades=10,
        )
        d = metrics_to_dict(m)
        assert d["bot_id"] == "day"
        assert d["total_return_pct"] == 5.12
        assert d["sharpe_ratio"] == 1.46
        assert d["win_rate"] == 0.667
        assert d["total_trades"] == 10

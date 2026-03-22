"""Walk-forward historical replay backtester for SwingSniper and DaySniper.

Replays historical candle data through the bot decision logic, simulating
order execution with realistic costs (60 bps taker fee + 10 bps slippage).
Used by the daily backtest cycle to score performance and feed the prompt optimizer.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import structlog

from engine.indicators import compute_all
from engine.regime import detect_regime

logger = structlog.get_logger(__name__)

FEE_BPS = 60
SLIPPAGE_BPS = 10
TOTAL_COST_BPS = FEE_BPS + SLIPPAGE_BPS


@dataclass
class SimTrade:
    pair: str
    side: str
    entry_price: float
    exit_price: float | None = None
    qty: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    hit_target: bool = False
    hit_stop: bool = False
    target_price: float = 0.0
    stop_price: float = 0.0


@dataclass
class BacktestMetrics:
    bot_id: str
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    avg_rr_achieved: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_hold_minutes: float = 0.0
    trades_per_day: float = 0.0
    total_trades: int = 0
    missed_opportunities: int = 0
    false_entries: int = 0
    trades: list[SimTrade] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.total_return_pct = float(self.total_return_pct)
        self.sharpe_ratio = float(self.sharpe_ratio)
        self.win_rate = float(self.win_rate)
        self.max_drawdown_pct = float(self.max_drawdown_pct)


def _apply_cost(price: float, side: str) -> float:
    """Apply taker fee + slippage to a simulated fill."""
    cost_mult = TOTAL_COST_BPS / 10000
    if side == "BUY":
        return price * (1 + cost_mult)
    return price * (1 - cost_mult)


async def replay_day_bot(
    candles_5m: list[dict],
    agent_fn,
    initial_capital: float = 10000.0,
    sample_every: int = 5,
) -> BacktestMetrics:
    """Replay DaySniper decisions against 5-minute candle history.

    agent_fn: async callable(indicators, candles, regime, portfolio) -> list[decision dicts]
    sample_every: only call AI every Nth candle to manage cost
    """
    metrics = BacktestMetrics(bot_id="day")
    if len(candles_5m) < 60:
        return metrics

    capital = initial_capital
    high_water = capital
    position: SimTrade | None = None
    daily_returns: list[float] = []
    prev_capital = capital

    for i in range(60, len(candles_5m)):
        candle = candles_5m[i]
        price = candle["close"]
        high = candle["high"]
        low = candle["low"]

        if position:
            if position.stop_price and low <= position.stop_price:
                exit_price = _apply_cost(position.stop_price, "SELL")
                position.exit_price = exit_price
                position.pnl = (exit_price - position.entry_price) * position.qty
                position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                position.hit_stop = True
                capital += position.qty * exit_price
                metrics.trades.append(position)
                position = None
                continue

            if position.target_price and high >= position.target_price:
                exit_price = _apply_cost(position.target_price, "SELL")
                position.exit_price = exit_price
                position.pnl = (exit_price - position.entry_price) * position.qty
                position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                position.hit_target = True
                capital += position.qty * exit_price
                metrics.trades.append(position)
                position = None
                continue

        if (i - 60) % sample_every != 0:
            continue

        history = candles_5m[max(0, i - 120):i]
        if len(history) < 30:
            continue

        try:
            indicators = compute_all(history)
            closes = [c["close"] for c in candles_5m[max(0, i - 300):i]]
            regime_state = detect_regime(closes) if len(closes) >= 48 else None
            regime_dict = {
                "label": regime_state.label,
                "confidence": regime_state.confidence,
                "features": regime_state.features,
            } if regime_state else {}

            portfolio = {"nav": capital + (position.qty * price if position else 0), "cash": capital}

            decisions = await agent_fn(indicators, history, regime_dict, portfolio)

            for d in decisions:
                if d.get("action") == "BUY" and not position and capital > 100:
                    alloc = min(capital * 0.2, capital)
                    entry = _apply_cost(price, "BUY")
                    qty = alloc / entry
                    capital -= qty * entry
                    position = SimTrade(
                        pair=d.get("pair", "BTC/USD"),
                        side="BUY",
                        entry_price=entry,
                        qty=qty,
                        target_price=d.get("target_price", 0),
                        stop_price=d.get("stop_price", 0),
                        entry_time=datetime.now(timezone.utc),
                    )
                elif d.get("action") == "SELL" and position:
                    exit_price = _apply_cost(price, "SELL")
                    position.exit_price = exit_price
                    position.pnl = (exit_price - position.entry_price) * position.qty
                    position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                    capital += position.qty * exit_price
                    metrics.trades.append(position)
                    position = None

        except Exception as e:
            logger.debug("backtest_tick_error", error=str(e))
            continue

        current_total = capital + (position.qty * price if position else 0)
        daily_return = (current_total - prev_capital) / prev_capital if prev_capital > 0 else 0.0
        daily_returns.append(daily_return)
        prev_capital = current_total

        if current_total > high_water:
            high_water = current_total
        dd = (high_water - current_total) / high_water * 100 if high_water > 0 else 0.0
        if dd > metrics.max_drawdown_pct:
            metrics.max_drawdown_pct = dd

    if position:
        final_price = candles_5m[-1]["close"]
        exit_price = _apply_cost(final_price, "SELL")
        position.exit_price = exit_price
        position.pnl = (exit_price - position.entry_price) * position.qty
        position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
        capital += position.qty * exit_price
        metrics.trades.append(position)

    final_value = capital
    metrics.total_return_pct = (final_value - initial_capital) / initial_capital * 100
    metrics.total_trades = len(metrics.trades)

    if metrics.trades:
        winners = [t for t in metrics.trades if t.pnl > 0]
        metrics.win_rate = float(len(winners) / len(metrics.trades))
        metrics.false_entries = sum(1 for t in metrics.trades if t.hit_stop)
        rr_values = []
        for t in metrics.trades:
            if t.stop_price and t.entry_price and t.stop_price != t.entry_price:
                risk = abs(t.entry_price - t.stop_price)
                reward = abs((t.exit_price or t.entry_price) - t.entry_price)
                if risk > 0:
                    rr_values.append(reward / risk)
        metrics.avg_rr_achieved = sum(rr_values) / len(rr_values) if rr_values else 0.0

    if daily_returns and len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns))
        metrics.sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

    candle_span_days = len(candles_5m) * 5 / (60 * 24)
    metrics.trades_per_day = metrics.total_trades / candle_span_days if candle_span_days > 0 else 0.0

    return metrics


async def replay_swing_bot(
    candles_4h: list[dict],
    agent_fn,
    initial_capital: float = 10000.0,
) -> BacktestMetrics:
    """Replay SwingSniper decisions against 4-hour candle history."""
    metrics = BacktestMetrics(bot_id="swing")
    if len(candles_4h) < 30:
        return metrics

    capital = initial_capital
    high_water = capital
    position: SimTrade | None = None
    daily_returns: list[float] = []
    prev_capital = capital

    for i in range(30, len(candles_4h)):
        candle = candles_4h[i]
        price = candle["close"]
        high = candle["high"]
        low = candle["low"]

        if position:
            if position.stop_price and low <= position.stop_price:
                exit_price = _apply_cost(position.stop_price, "SELL")
                position.exit_price = exit_price
                position.pnl = (exit_price - position.entry_price) * position.qty
                position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                position.hit_stop = True
                capital += position.qty * exit_price
                metrics.trades.append(position)
                position = None
                continue

            if position.target_price and high >= position.target_price:
                exit_price = _apply_cost(position.target_price, "SELL")
                position.exit_price = exit_price
                position.pnl = (exit_price - position.entry_price) * position.qty
                position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                position.hit_target = True
                capital += position.qty * exit_price
                metrics.trades.append(position)
                position = None
                continue

        history = candles_4h[max(0, i - 90):i]
        if len(history) < 20:
            continue

        try:
            indicators = compute_all(history)
            closes = [c["close"] for c in candles_4h[max(0, i - 200):i]]
            regime_state = detect_regime(closes) if len(closes) >= 48 else None
            regime_dict = {
                "label": regime_state.label,
                "confidence": regime_state.confidence,
                "features": regime_state.features,
            } if regime_state else {}

            portfolio = {"nav": capital + (position.qty * price if position else 0), "cash": capital}

            decisions = await agent_fn(indicators, history, regime_dict, portfolio)

            for d in decisions:
                if d.get("action") == "BUY" and not position and capital > 100:
                    alloc = min(capital * 0.3, capital)
                    entry = _apply_cost(price, "BUY")
                    qty = alloc / entry
                    capital -= qty * entry
                    position = SimTrade(
                        pair=d.get("pair", "BTC/USD"),
                        side="BUY",
                        entry_price=entry,
                        qty=qty,
                        target_price=d.get("target_price", 0),
                        stop_price=d.get("stop_price", 0),
                        entry_time=datetime.now(timezone.utc),
                    )
                elif d.get("action") == "SELL" and position:
                    exit_price = _apply_cost(price, "SELL")
                    position.exit_price = exit_price
                    position.pnl = (exit_price - position.entry_price) * position.qty
                    position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                    capital += position.qty * exit_price
                    metrics.trades.append(position)
                    position = None

        except Exception as e:
            logger.debug("swing_backtest_tick_error", error=str(e))
            continue

        current_total = capital + (position.qty * price if position else 0)
        daily_return = (current_total - prev_capital) / prev_capital if prev_capital > 0 else 0.0
        daily_returns.append(daily_return)
        prev_capital = current_total

        if current_total > high_water:
            high_water = current_total
        dd = (high_water - current_total) / high_water * 100 if high_water > 0 else 0.0
        if dd > metrics.max_drawdown_pct:
            metrics.max_drawdown_pct = dd

    if position:
        final_price = candles_4h[-1]["close"]
        exit_price = _apply_cost(final_price, "SELL")
        position.exit_price = exit_price
        position.pnl = (exit_price - position.entry_price) * position.qty
        position.pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
        capital += position.qty * exit_price
        metrics.trades.append(position)

    final_value = capital
    metrics.total_return_pct = (final_value - initial_capital) / initial_capital * 100
    metrics.total_trades = len(metrics.trades)

    if metrics.trades:
        winners = [t for t in metrics.trades if t.pnl > 0]
        metrics.win_rate = float(len(winners) / len(metrics.trades))
        metrics.false_entries = sum(1 for t in metrics.trades if t.hit_stop)

    if daily_returns and len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns))
        metrics.sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

    candle_span_days = len(candles_4h) * 4 / 24
    metrics.trades_per_day = metrics.total_trades / candle_span_days if candle_span_days > 0 else 0.0

    return metrics


def metrics_to_dict(m: BacktestMetrics) -> dict:
    return {
        "bot_id": m.bot_id,
        "total_return_pct": round(m.total_return_pct, 2),
        "sharpe_ratio": round(m.sharpe_ratio, 2),
        "win_rate": round(m.win_rate, 3),
        "avg_rr_achieved": round(m.avg_rr_achieved, 2),
        "max_drawdown_pct": round(m.max_drawdown_pct, 2),
        "trades_per_day": round(m.trades_per_day, 2),
        "total_trades": m.total_trades,
        "false_entries": m.false_entries,
    }

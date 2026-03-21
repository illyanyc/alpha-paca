"""Lightweight backtester — runs strategies on historical bars and scores them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog

from engine.indicators import compute_all
from engine.strategies import ALL_STRATEGIES

logger = structlog.get_logger(__name__)

BACKTEST_CACHE_KEY = "crypto:backtest:results"
BACKTEST_TTL = 3600


@dataclass
class TradeRecord:
    entry_price: float
    entry_bar: int
    exit_price: float = 0
    exit_bar: int = 0
    pnl_pct: float = 0
    closed: bool = False


@dataclass
class StrategyResult:
    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pct: float = 0
    max_drawdown_pct: float = 0
    avg_trade_pnl_pct: float = 0
    win_rate: float = 0
    sharpe: float = 0.0
    weight: float = 0.25

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl_pct": round(self.total_pnl_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 3),
            "avg_trade_pnl_pct": round(self.avg_trade_pnl_pct, 3),
            "win_rate": round(self.win_rate, 3),
            "sharpe": round(self.sharpe, 3),
            "weight": round(self.weight, 3),
        }


def backtest_strategy(
    name: str,
    strategy_fn,
    bars: list[dict],
    min_bars: int = 30,
) -> StrategyResult:
    """Walk-forward backtest a single strategy on historical bars.

    Simulates buy/sell decisions bar-by-bar. Holds one position at a time.
    Exits after 10 bars max (aggressive scalp horizon).
    """
    result = StrategyResult(name=name)
    if len(bars) < min_bars + 10:
        return result

    trades: list[TradeRecord] = []
    position: TradeRecord | None = None
    equity_curve: list[float] = [0.0]
    pnl_per_trade: list[float] = []

    MAX_HOLD_BARS = 15

    for i in range(min_bars, len(bars)):
        window = bars[max(0, i - 120):i + 1]
        indicators = compute_all(window)
        sig = strategy_fn(window, indicators)

        current_price = bars[i]["close"]

        if position and not position.closed:
            bars_held = i - position.entry_bar
            unrealized = (current_price - position.entry_price) / position.entry_price

            should_exit = (
                sig["signal"] == "sell"
                or bars_held >= MAX_HOLD_BARS
                or unrealized <= -0.03  # 3% stop loss
                or unrealized >= 0.05   # 5% take profit
            )

            if should_exit:
                position.exit_price = current_price
                position.exit_bar = i
                position.pnl_pct = unrealized * 100
                position.closed = True
                trades.append(position)
                pnl_per_trade.append(position.pnl_pct)
                position = None

        elif position is None and sig["signal"] == "buy" and sig["confidence"] > 0.3:
            position = TradeRecord(entry_price=current_price, entry_bar=i)

        cum_pnl = sum(pnl_per_trade)
        equity_curve.append(cum_pnl)

    if position and not position.closed:
        position.exit_price = bars[-1]["close"]
        position.exit_bar = len(bars) - 1
        position.pnl_pct = (position.exit_price - position.entry_price) / position.entry_price * 100
        position.closed = True
        trades.append(position)
        pnl_per_trade.append(position.pnl_pct)

    result.total_trades = len(trades)
    result.wins = sum(1 for t in trades if t.pnl_pct > 0)
    result.losses = result.total_trades - result.wins
    result.total_pnl_pct = sum(t.pnl_pct for t in trades)
    result.avg_trade_pnl_pct = result.total_pnl_pct / max(result.total_trades, 1)
    result.win_rate = result.wins / max(result.total_trades, 1)

    if equity_curve:
        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd

    if len(pnl_per_trade) > 1:
        mean_r = sum(pnl_per_trade) / len(pnl_per_trade)
        var_r = sum((r - mean_r) ** 2 for r in pnl_per_trade) / len(pnl_per_trade)
        std_r = var_r ** 0.5
        result.sharpe = mean_r / std_r if std_r > 0 else 0

    return result


def backtest_all(bars: list[dict]) -> list[StrategyResult]:
    """Backtest every registered strategy and return sorted results."""
    results = []
    for name, fn in ALL_STRATEGIES.items():
        res = backtest_strategy(name, fn, bars)
        results.append(res)
    return sorted(results, key=lambda r: r.sharpe, reverse=True)


def compute_strategy_weights(results: list[StrategyResult]) -> dict[str, float]:
    """Compute normalized weights based on Sharpe ratio (positive only).

    Returns a dict mapping strategy name to weight (0-1, summing to 1).
    Strategies with negative Sharpe get minimum weight.
    """
    MIN_WEIGHT = 0.05
    sharpes = {r.name: max(r.sharpe, 0) for r in results}

    total = sum(sharpes.values())
    if total <= 0:
        n = len(results)
        return {r.name: 1.0 / n for r in results}

    weights = {}
    for r in results:
        raw = sharpes[r.name] / total
        weights[r.name] = max(raw, MIN_WEIGHT)

    total_w = sum(weights.values())
    return {k: v / total_w for k, v in weights.items()}


async def run_backtest_cycle(
    pairs: list[str],
    get_bars_fn,
    redis_conn: aioredis.Redis,
) -> dict[str, Any]:
    """Run backtests for all strategies across all pairs, cache results to Redis.

    Returns the aggregated strategy weights and per-pair/per-strategy results.
    """
    import asyncio

    all_pair_results: dict[str, list[dict]] = {}
    aggregate_sharpes: dict[str, list[float]] = {name: [] for name in ALL_STRATEGIES}

    for pair in pairs:
        try:
            bars = await asyncio.to_thread(get_bars_fn, pair, lookback_minutes=1440 * 3)
            if len(bars) < 60:
                continue

            pair_results = await asyncio.to_thread(backtest_all, bars)
            all_pair_results[pair] = [r.to_dict() for r in pair_results]

            for r in pair_results:
                aggregate_sharpes[r.name].append(r.sharpe)

        except Exception:
            logger.exception("backtest_failed", pair=pair)

    avg_results = []
    for name in ALL_STRATEGIES:
        sharpes = aggregate_sharpes[name]
        avg_sharpe = sum(sharpes) / max(len(sharpes), 1)
        avg_results.append(StrategyResult(name=name, sharpe=avg_sharpe))

    weights = compute_strategy_weights(avg_results)
    for r in avg_results:
        r.weight = weights.get(r.name, 0.25)

    output = {
        "strategy_weights": weights,
        "per_pair": all_pair_results,
        "aggregate": [r.to_dict() for r in sorted(avg_results, key=lambda x: x.sharpe, reverse=True)],
    }

    await redis_conn.set(BACKTEST_CACHE_KEY, json.dumps(output), ex=BACKTEST_TTL)
    logger.info("backtest_complete", strategies=len(weights), pairs=len(all_pair_results))

    return output

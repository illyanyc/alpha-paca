"""Walk-forward backtester with transaction costs, slippage, multi-position support,
and per-regime strategy performance tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog

from engine.indicators import compute_all
from engine.regime import Regime, detect_regime
from engine.strategies import ALL_STRATEGIES

logger = structlog.get_logger(__name__)

BACKTEST_CACHE_KEY = "crypto:backtest:results"
BACKTEST_TTL = 3600

TAKER_FEE_BPS = 60
SLIPPAGE_BPS = 10


def _fee_and_slippage(price: float) -> float:
    return price * (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10000


@dataclass
class TradeRecord:
    pair: str
    strategy: str
    entry_price: float
    entry_bar: int
    direction: int = 1
    exit_price: float = 0
    exit_bar: int = 0
    pnl_pct: float = 0
    pnl_usd: float = 0
    closed: bool = False
    regime_at_entry: str = ""
    notional: float = 1000


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
    per_regime: dict[str, dict] = field(default_factory=dict)

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
            "per_regime": self.per_regime,
        }


def _compute_regime_from_bars(bars: list[dict], index: int) -> str:
    if index < 72:
        return "volatile"
    hourly_closes = [b["close"] for b in bars[max(0, index - 168):index + 1]]
    state = detect_regime(hourly_closes)
    return state.regime.value


def backtest_strategy(
    name: str,
    strategy_fn,
    bars: list[dict],
    pair: str = "UNKNOWN",
    min_bars: int = 30,
    allow_concurrent: bool = True,
) -> StrategyResult:
    """Walk-forward backtest with transaction costs, slippage, and multi-position support."""
    result = StrategyResult(name=name)
    if len(bars) < min_bars + 10:
        return result

    trades: list[TradeRecord] = []
    open_positions: list[TradeRecord] = []
    equity_curve: list[float] = [0.0]
    pnl_per_trade: list[float] = []
    regime_trades: dict[str, list[float]] = {}

    MAX_HOLD_BARS = 20
    MAX_CONCURRENT = 3 if allow_concurrent else 1
    STOP_LOSS_PCT = -0.03
    TAKE_PROFIT_PCT = 0.05

    for i in range(min_bars, len(bars)):
        window = bars[max(0, i - 120):i + 1]
        indicators = compute_all(window)
        sig = strategy_fn(window, indicators)
        current_price = bars[i]["close"]

        closed_this_bar = []
        for pos in open_positions:
            if pos.closed:
                continue
            bars_held = i - pos.entry_bar
            gross_return = (current_price - pos.entry_price) / pos.entry_price * pos.direction
            entry_cost = _fee_and_slippage(pos.entry_price)
            exit_cost = _fee_and_slippage(current_price)
            net_return = gross_return - (entry_cost + exit_cost) / pos.entry_price

            should_exit = (
                (sig["signal"] == "sell" and pos.direction == 1)
                or (sig["signal"] == "buy" and pos.direction == -1)
                or bars_held >= MAX_HOLD_BARS
                or net_return <= STOP_LOSS_PCT
                or net_return >= TAKE_PROFIT_PCT
            )

            if should_exit:
                pos.exit_price = current_price
                pos.exit_bar = i
                pos.pnl_pct = net_return * 100
                pos.pnl_usd = pos.notional * net_return
                pos.closed = True
                trades.append(pos)
                pnl_per_trade.append(pos.pnl_pct)
                closed_this_bar.append(pos)

                regime_key = pos.regime_at_entry
                if regime_key not in regime_trades:
                    regime_trades[regime_key] = []
                regime_trades[regime_key].append(pos.pnl_pct)

        open_positions = [p for p in open_positions if not p.closed]

        if len(open_positions) < MAX_CONCURRENT:
            if sig["signal"] == "buy" and sig.get("confidence", 0) > 0.3:
                regime = _compute_regime_from_bars(bars, i) if i >= 72 else "volatile"
                open_positions.append(TradeRecord(
                    pair=pair, strategy=name, entry_price=current_price,
                    entry_bar=i, direction=1, regime_at_entry=regime,
                ))
            elif sig["signal"] == "sell" and sig.get("confidence", 0) > 0.3:
                regime = _compute_regime_from_bars(bars, i) if i >= 72 else "volatile"
                open_positions.append(TradeRecord(
                    pair=pair, strategy=name, entry_price=current_price,
                    entry_bar=i, direction=-1, regime_at_entry=regime,
                ))

        cum_pnl = sum(pnl_per_trade)
        equity_curve.append(cum_pnl)

    for pos in open_positions:
        if not pos.closed:
            pos.exit_price = bars[-1]["close"]
            pos.exit_bar = len(bars) - 1
            gross = (pos.exit_price - pos.entry_price) / pos.entry_price * pos.direction
            costs = (_fee_and_slippage(pos.entry_price) + _fee_and_slippage(pos.exit_price)) / pos.entry_price
            pos.pnl_pct = (gross - costs) * 100
            pos.closed = True
            trades.append(pos)
            pnl_per_trade.append(pos.pnl_pct)

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

    for regime, pnls in regime_trades.items():
        wins = sum(1 for p in pnls if p > 0)
        result.per_regime[regime] = {
            "trades": len(pnls),
            "wins": wins,
            "win_rate": round(wins / max(len(pnls), 1), 3),
            "avg_pnl": round(sum(pnls) / max(len(pnls), 1), 3),
        }

    return result


def backtest_all(bars: list[dict], pair: str = "UNKNOWN") -> list[StrategyResult]:
    results = []
    for name, fn in ALL_STRATEGIES.items():
        res = backtest_strategy(name, fn, bars, pair=pair)
        results.append(res)
    return sorted(results, key=lambda r: r.sharpe, reverse=True)


def compute_strategy_weights(results: list[StrategyResult]) -> dict[str, float]:
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
    import asyncio

    all_pair_results: dict[str, list[dict]] = {}
    aggregate_sharpes: dict[str, list[float]] = {name: [] for name in ALL_STRATEGIES}

    for pair in pairs:
        try:
            bars = await asyncio.to_thread(get_bars_fn, pair, lookback_minutes=1440 * 3)
            if len(bars) < 60:
                continue

            pair_results = await asyncio.to_thread(backtest_all, bars, pair)
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
        r.weight = weights.get(r.name, 0.125)

    output = {
        "strategy_weights": weights,
        "per_pair": all_pair_results,
        "aggregate": [r.to_dict() for r in sorted(avg_results, key=lambda x: x.sharpe, reverse=True)],
    }

    await redis_conn.set(BACKTEST_CACHE_KEY, json.dumps(output), ex=BACKTEST_TTL)
    logger.info("backtest_complete", strategies=len(weights), pairs=len(all_pair_results))

    return output

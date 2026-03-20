"""Vectorised backtesting engine for signal evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_INITIAL_CAPITAL: float = 10_000.0


@dataclass
class BacktestParams:
    slippage_bps: float = 5.0
    commission_per_share: float = 0.005
    position_size_pct: float = 5.0


@dataclass
class BacktestMetrics:
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    total_return: float = 0.0


class Backtester:
    """Simple vectorised backtester for signal-level evaluation."""

    def __init__(self, initial_capital: float = DEFAULT_INITIAL_CAPITAL) -> None:
        self._initial_capital = initial_capital

    def run_backtest(
        self,
        signals: np.ndarray,
        prices: np.ndarray,
        params: BacktestParams | None = None,
    ) -> np.ndarray:
        """Simulate trading and return an equity curve.

        ``signals`` — 1-D array of {-1, 0, +1} per bar.
        ``prices``  — 1-D array of close prices aligned to signals.
        """
        if params is None:
            params = BacktestParams()

        n = len(prices)
        equity = np.full(n, self._initial_capital)
        position = 0.0
        cash = self._initial_capital

        slippage_mult = 1.0 + params.slippage_bps / 10_000

        for i in range(1, n):
            desired = int(signals[i])
            price = prices[i]

            if desired != int(np.sign(position)):
                if position != 0:
                    cash += position * price / slippage_mult
                    position = 0.0
                if desired != 0:
                    trade_value = cash * (params.position_size_pct / 100)
                    shares = trade_value / (price * slippage_mult)
                    position = shares * desired
                    cash -= abs(position) * price * slippage_mult
                    cash -= abs(shares) * params.commission_per_share

            equity[i] = cash + position * price

        return equity

    @staticmethod
    def compute_metrics(equity_curve: np.ndarray) -> BacktestMetrics:
        """Derive Sharpe, max-drawdown, win-rate, and profit factor."""
        returns = np.diff(equity_curve) / equity_curve[:-1]
        returns = returns[np.isfinite(returns)]

        if len(returns) == 0:
            return BacktestMetrics()

        mean_r = float(np.mean(returns))
        std_r = float(np.std(returns))
        sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0

        running_max = np.maximum.accumulate(equity_curve)
        drawdowns = (equity_curve - running_max) / running_max
        max_dd = float(np.min(drawdowns))

        wins = returns[returns > 0]
        losses = returns[returns < 0]
        win_rate = float(len(wins) / len(returns)) if len(returns) > 0 else 0.0
        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-9
        profit_factor = gross_profit / gross_loss

        total_return = float(
            (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
        )

        return BacktestMetrics(
            sharpe=sharpe,
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=int(len(returns)),
            total_return=total_return,
        )

    @staticmethod
    def split_oos(
        data: np.ndarray,
        train_pct: float = 0.7,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Split into train / out-of-sample segments."""
        split_idx = int(len(data) * train_pct)
        return data[:split_idx], data[split_idx:]

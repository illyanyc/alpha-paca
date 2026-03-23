"""Risk-constrained Kelly criterion position sizer."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from app.config import get_settings
from app.engine.regime.models import RegimeState

logger = structlog.get_logger(__name__)

REGIME_MAX_POSITION_PCT: dict[RegimeState, float] = {
    RegimeState.BULL_TREND: 25.0,
    RegimeState.BEAR_TREND: 15.0,
    RegimeState.SIDEWAYS: 15.0,
    RegimeState.CRISIS: 5.0,
}

MIN_RISK_PER_TRADE_PCT = 0.5
MAX_RISK_PER_TRADE_PCT = 3.0
DEFAULT_KELLY_FRACTION = 0.25


class KellySizer:
    """Computes optimal position size using the Kelly criterion with safety constraints."""

    def __init__(
        self,
        kelly_fraction: float = DEFAULT_KELLY_FRACTION,
        trade_history: list[dict[str, Any]] | None = None,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._trade_history = trade_history or []

    def compute_kelly_fraction(self, pod_name: str) -> float:
        """Compute f* = (W*R - (1-W)) / R from recent trade history."""
        pod_trades = [t for t in self._trade_history if t.get("pod_name") == pod_name]
        if len(pod_trades) < 20:
            return self._kelly_fraction * MIN_RISK_PER_TRADE_PCT / 100

        wins = [t for t in pod_trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in pod_trades if (t.get("pnl") or 0) <= 0]

        if not wins or not losses:
            return self._kelly_fraction * MIN_RISK_PER_TRADE_PCT / 100

        win_rate = len(wins) / len(pod_trades)
        avg_win = np.mean([abs(t.get("pnl_pct") or t.get("pnl", 0)) for t in wins])
        avg_loss = np.mean([abs(t.get("pnl_pct") or t.get("pnl", 0)) for t in losses])

        if avg_loss == 0:
            return self._kelly_fraction * MAX_RISK_PER_TRADE_PCT / 100

        win_loss_ratio = avg_win / avg_loss
        kelly_f = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio

        fractional_kelly = kelly_f * self._kelly_fraction

        risk_pct = np.clip(fractional_kelly * 100, MIN_RISK_PER_TRADE_PCT, MAX_RISK_PER_TRADE_PCT)
        return risk_pct / 100

    def compute_position_size(
        self,
        signal: dict[str, Any],
        portfolio_nav: float,
        pod_name: str,
        regime: RegimeState | None = None,
    ) -> float:
        """Kelly-based position sizing with regime-conditional caps.

        Returns position size as percentage of NAV (0-100).
        """
        settings = get_settings()
        entry = signal.get("entry_price", 0.0)
        stop = signal.get("stop_loss", 0.0)
        if entry == 0 or stop == 0 or portfolio_nav <= 0:
            return 0.0

        risk_per_share = abs(entry - stop)
        if risk_per_share == 0:
            return 0.0

        kelly_risk_pct = self.compute_kelly_fraction(pod_name)
        dollar_risk = portfolio_nav * kelly_risk_pct
        shares = dollar_risk / risk_per_share
        position_value = shares * entry
        position_pct = (position_value / portfolio_nav) * 100

        max_pct = settings.position_sizing.max_position_pct
        if regime is not None:
            max_pct = min(max_pct, REGIME_MAX_POSITION_PCT.get(regime, max_pct))

        return min(position_pct, max_pct)

    def update_trade_history(self, trades: list[dict[str, Any]]) -> None:
        """Replace internal trade history with fresh data."""
        self._trade_history = trades[-500:]

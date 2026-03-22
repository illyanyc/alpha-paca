"""Conviction-based leverage/position sizer for the two-bot system.

Maps AI conviction level to effective leverage (via position size on spot),
with ATR volatility scaling and anti-martingale adjustments.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from config import get_settings


@dataclass
class SizedOrder:
    pair: str
    notional_usd: float
    pct_of_capital: float
    effective_leverage: float
    adjustments: dict[str, float] = field(default_factory=dict)


class ConsecutiveLossTracker:
    """Tracks recent trade results per bot for anti-martingale logic."""

    def __init__(self, max_entries: int = 100) -> None:
        self._results: deque[tuple[str, str, bool]] = deque(maxlen=max_entries)

    def record(self, bot_id: str, pair: str, won: bool) -> None:
        self._results.append((bot_id, pair, won))

    def consecutive_losses(self, bot_id: str) -> int:
        count = 0
        for b, _, won in reversed(self._results):
            if b != bot_id:
                continue
            if not won:
                count += 1
            else:
                break
        return count


_loss_tracker = ConsecutiveLossTracker()


def get_loss_tracker() -> ConsecutiveLossTracker:
    return _loss_tracker


LEVERAGE_TIERS: list[tuple[float, float, float]] = [
    # (min_conviction, max_conviction, effective_leverage)
    (0.75, 0.84, 1.0),
    (0.85, 0.89, 2.0),
    (0.90, 0.94, 3.0),
    (0.95, 1.00, 5.0),
]

BASE_ALLOCATION = 0.20


def _conviction_to_leverage(conviction: float) -> float:
    for lo, hi, lev in LEVERAGE_TIERS:
        if lo <= conviction <= hi:
            return lev
    return 0.0


def _atr_volatility_scalar(atr_value: float | None, price: float) -> float:
    """Reduce size in high-volatility environments."""
    if not atr_value or price <= 0:
        return 1.0
    atr_pct = atr_value / price
    if atr_pct > 0.05:
        return 0.4
    if atr_pct > 0.04:
        return 0.5
    if atr_pct > 0.03:
        return 0.65
    if atr_pct > 0.02:
        return 0.8
    return 1.0


def _anti_martingale_scalar(bot_id: str) -> float:
    """Reduce size after consecutive losses."""
    losses = _loss_tracker.consecutive_losses(bot_id)
    if losses >= 5:
        return 0.3
    if losses >= 3:
        return 0.5
    if losses >= 2:
        return 0.7
    return 1.0


def compute_leverage_size(
    pair: str,
    conviction: float,
    bot_id: str,
    available_capital: float,
    atr_value: float | None = None,
    price: float = 0.0,
) -> SizedOrder | None:
    """Full position sizing pipeline. Returns None if conviction too low."""
    settings = get_settings()

    if conviction < settings.crypto.min_conviction:
        return None

    leverage = _conviction_to_leverage(conviction)
    if leverage <= 0:
        return None

    max_lev = settings.crypto.max_leverage
    leverage = min(leverage, max_lev)

    target_pct = BASE_ALLOCATION * leverage

    adjustments: dict[str, float] = {"base_leverage": leverage}

    vol_s = _atr_volatility_scalar(atr_value, price)
    adjustments["volatility"] = vol_s
    target_pct *= vol_s

    am_s = _anti_martingale_scalar(bot_id)
    adjustments["anti_martingale"] = am_s
    target_pct *= am_s

    max_risk_pct = settings.crypto.max_risk_per_trade_pct / 100.0
    target_pct = min(target_pct, max_risk_pct * leverage)

    target_pct = max(0.01, min(target_pct, 1.0))

    notional = available_capital * target_pct

    return SizedOrder(
        pair=pair,
        notional_usd=notional,
        pct_of_capital=target_pct * 100,
        effective_leverage=leverage,
        adjustments=adjustments,
    )

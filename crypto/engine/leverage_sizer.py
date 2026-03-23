"""ATR-based fixed-fractional position sizer.

Sizes positions so that the ATR trailing stop represents exactly
risk_per_trade_pct of account NAV.  Keeps anti-martingale scaling
from the previous system.

Formula:  position_size = risk_amount / (atr * stop_multiplier)
  where risk_amount = account_nav * risk_per_trade_pct / 100
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
    stop_distance: float
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


def _atr_volatility_scalar(atr_value: float | None, price: float) -> float:
    """Reduce size in extreme volatility environments."""
    if not atr_value or price <= 0:
        return 1.0
    atr_pct = atr_value / price
    if atr_pct > 0.05:
        return 0.5
    if atr_pct > 0.04:
        return 0.6
    if atr_pct > 0.03:
        return 0.75
    return 1.0


def compute_position_size(
    pair: str,
    bot_id: str,
    account_nav: float,
    entry_price: float,
    atr_value: float,
    risk_per_trade_pct: float | None = None,
    atr_stop_multiplier: float | None = None,
) -> SizedOrder | None:
    """ATR-based fixed-fractional sizing.

    Sizes so that hitting the ATR trailing stop loses exactly
    risk_per_trade_pct of account NAV.
    """
    settings = get_settings()
    risk_pct = risk_per_trade_pct or settings.crypto.max_risk_per_trade_pct
    stop_mult = atr_stop_multiplier or settings.crypto.atr_stop_multiplier

    if account_nav <= 0 or entry_price <= 0 or not atr_value or atr_value <= 0:
        return None

    risk_amount = account_nav * (risk_pct / 100.0)

    stop_distance = atr_value * stop_mult
    stop_distance_pct = stop_distance / entry_price

    if stop_distance_pct <= 0:
        return None

    raw_notional = risk_amount / stop_distance_pct

    adjustments: dict[str, float] = {"base_risk_pct": risk_pct}

    vol_scalar = _atr_volatility_scalar(atr_value, entry_price)
    adjustments["volatility"] = vol_scalar
    raw_notional *= vol_scalar

    am_scalar = _anti_martingale_scalar(bot_id)
    adjustments["anti_martingale"] = am_scalar
    raw_notional *= am_scalar

    max_single_position = account_nav * 0.33
    raw_notional = min(raw_notional, max_single_position)

    raw_notional = max(10.0, raw_notional)

    pct_of_capital = (raw_notional / account_nav * 100) if account_nav > 0 else 0
    effective_leverage = raw_notional / account_nav if account_nav > 0 else 0

    return SizedOrder(
        pair=pair,
        notional_usd=raw_notional,
        pct_of_capital=round(pct_of_capital, 2),
        effective_leverage=round(effective_leverage, 3),
        stop_distance=round(stop_distance, 4),
        adjustments=adjustments,
    )


def compute_leverage_size(
    pair: str,
    conviction: float,
    bot_id: str,
    available_capital: float,
    atr_value: float | None = None,
    price: float = 0.0,
) -> SizedOrder | None:
    """Backward-compatible entry point used by main.py.

    For the new Adaptive Momentum strategy, conviction is not used for
    leverage tiers — we use ATR-based fixed-fractional sizing instead.
    Falls back to a percentage-of-capital approach when ATR is unavailable.
    """
    settings = get_settings()

    if conviction < settings.crypto.min_conviction:
        return None

    if atr_value and atr_value > 0 and price > 0:
        return compute_position_size(
            pair=pair,
            bot_id=bot_id,
            account_nav=available_capital,
            entry_price=price,
            atr_value=atr_value,
        )

    risk_pct = settings.crypto.max_risk_per_trade_pct / 100.0
    notional = available_capital * risk_pct * 5
    notional = min(notional, available_capital * 0.33)

    adjustments: dict[str, float] = {"fallback": True}
    am_s = _anti_martingale_scalar(bot_id)
    notional *= am_s
    adjustments["anti_martingale"] = am_s

    return SizedOrder(
        pair=pair,
        notional_usd=max(10.0, notional),
        pct_of_capital=round(notional / available_capital * 100, 2) if available_capital > 0 else 0,
        effective_leverage=round(notional / available_capital, 3) if available_capital > 0 else 0,
        stop_distance=0.0,
        adjustments=adjustments,
    )

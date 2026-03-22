"""Advanced position sizing — Kelly/ATR with volatility scaling, regime adjustment,
correlation penalty, and anti-martingale.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from config import get_settings


@dataclass
class PositionSize:
    pair: str
    qty: float
    notional_usd: float
    pct_of_capital: float
    method: str
    adjustments: dict[str, float] = field(default_factory=dict)


class TradeResultTracker:
    """Tracks recent trade results for anti-martingale logic."""

    def __init__(self, max_entries: int = 50) -> None:
        self._results: deque[tuple[str, bool]] = deque(maxlen=max_entries)

    def record(self, pair: str, won: bool) -> None:
        self._results.append((pair, won))

    def consecutive_losses(self) -> int:
        count = 0
        for _, won in reversed(self._results):
            if not won:
                count += 1
            else:
                break
        return count

    def pair_consecutive_losses(self, pair: str) -> int:
        count = 0
        for p, won in reversed(self._results):
            if p != pair:
                continue
            if not won:
                count += 1
            else:
                break
        return count


_trade_tracker = TradeResultTracker()


def get_trade_tracker() -> TradeResultTracker:
    return _trade_tracker


def fractional_kelly(
    win_prob: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.35,
) -> float:
    """Fractional Kelly criterion — returns optimal bet fraction (0 to 1)."""
    if avg_loss == 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    b = avg_win / avg_loss
    kelly_full = (win_prob * b - (1 - win_prob)) / b
    return max(0.0, kelly_full * fraction)


def _volatility_scalar(atr_value: float | None, price: float) -> float:
    """Inverse volatility scaling — reduce size when vol is high."""
    if not atr_value or price <= 0:
        return 1.0
    atr_pct = atr_value / price
    if atr_pct > 0.04:
        return 0.5
    elif atr_pct > 0.03:
        return 0.65
    elif atr_pct > 0.02:
        return 0.8
    elif atr_pct < 0.005:
        return 1.3
    return 1.0


def _regime_scalar(regime: str | None) -> float:
    """Scale position based on regime confidence."""
    if not regime:
        return 1.0
    scalars = {
        "trending_up": 1.2,
        "trending_down": 1.1,
        "mean_reverting": 1.0,
        "volatile": 0.7,
    }
    return scalars.get(regime, 1.0)


def _correlation_penalty(pair: str, open_positions: list[dict]) -> float:
    """Reduce size when we have 3+ correlated positions open."""
    correlated_groups = {
        "BTC-corr": {"BTC/USD", "ETH/USD"},
        "alt-coins": {"SOL/USD", "LINK/USD", "DOGE/USD", "ALGO/USD"},
    }

    my_group_pairs: set[str] = set()
    for _, members in correlated_groups.items():
        if pair in members:
            my_group_pairs = members
            break

    if not my_group_pairs:
        return 1.0

    correlated_count = sum(
        1 for p in open_positions
        if p.get("pair", p.get("symbol", "")) in my_group_pairs
    )

    if correlated_count >= 3:
        return 0.5
    elif correlated_count >= 2:
        return 0.7
    return 1.0


def _anti_martingale_scalar(pair: str) -> float:
    """Reduce size after consecutive losses (anti-martingale)."""
    tracker = get_trade_tracker()
    global_losses = tracker.consecutive_losses()
    pair_losses = tracker.pair_consecutive_losses(pair)

    if global_losses >= 5:
        return 0.4
    elif global_losses >= 3:
        return 0.65
    elif pair_losses >= 3:
        return 0.65
    elif pair_losses >= 2:
        return 0.8
    return 1.0


def compute_position_size(
    pair: str,
    price: float,
    confidence: float,
    atr_value: float | None,
    available_capital: float,
    current_exposure_pct: float,
    regime: str | None = None,
    open_positions: list[dict] | None = None,
) -> PositionSize:
    """Full position sizing pipeline with institutional-grade adjustments."""
    settings = get_settings()
    risk_per_trade = settings.crypto.risk_per_trade_pct / 100
    max_position = settings.crypto.max_position_pct / 100
    max_exposure = settings.crypto.max_total_exposure_pct / 100

    remaining_exposure = max(0, max_exposure - current_exposure_pct / 100)
    cap_for_position = min(max_position, remaining_exposure)

    if atr_value and atr_value > 0 and price > 0:
        risk_usd = available_capital * risk_per_trade
        stop_distance = atr_value * 1.5
        atr_qty = risk_usd / stop_distance
        atr_notional = atr_qty * price
        atr_pct = atr_notional / available_capital if available_capital > 0 else 0
    else:
        atr_pct = risk_per_trade
        atr_notional = available_capital * atr_pct

    kelly_est = fractional_kelly(
        win_prob=0.5 + confidence * 0.15,
        avg_win=1.5,
        avg_loss=1.0,
        fraction=0.25,
    )

    target_pct = min(atr_pct, kelly_est, cap_for_position)
    target_pct = max(target_pct, 0.01)

    adjustments: dict[str, float] = {}

    vol_s = _volatility_scalar(atr_value, price)
    adjustments["volatility"] = vol_s
    target_pct *= vol_s

    reg_s = _regime_scalar(regime)
    adjustments["regime"] = reg_s
    target_pct *= reg_s

    corr_s = _correlation_penalty(pair, open_positions or [])
    adjustments["correlation"] = corr_s
    target_pct *= corr_s

    am_s = _anti_martingale_scalar(pair)
    adjustments["anti_martingale"] = am_s
    target_pct *= am_s

    target_pct = max(0.01, min(target_pct, cap_for_position))

    notional = available_capital * target_pct
    qty = notional / price if price > 0 else 0

    return PositionSize(
        pair=pair,
        qty=qty,
        notional_usd=notional,
        pct_of_capital=target_pct * 100,
        method="Kelly+ATR+vol+regime+corr+antimart",
        adjustments=adjustments,
    )

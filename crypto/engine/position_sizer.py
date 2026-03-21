"""Kelly/fractional-Kelly position sizing for crypto trades."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import get_settings


@dataclass
class PositionSize:
    pair: str
    qty: float
    notional_usd: float
    pct_of_capital: float
    method: str


def fractional_kelly(
    win_prob: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.25,
) -> float:
    """Fractional Kelly criterion — returns optimal bet fraction (0 to 1)."""
    if avg_loss == 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    b = avg_win / avg_loss
    kelly_full = (win_prob * b - (1 - win_prob)) / b
    return max(0.0, kelly_full * fraction)


def compute_position_size(
    pair: str,
    price: float,
    confidence: float,
    atr_value: float | None,
    available_capital: float,
    current_exposure_pct: float,
) -> PositionSize:
    """Determine the position size given risk parameters and market conditions."""
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
    kelly_pct = kelly_est

    target_pct = min(atr_pct, kelly_pct, cap_for_position)
    target_pct = max(target_pct, 0.005)  # min 0.5%

    notional = available_capital * target_pct
    qty = notional / price if price > 0 else 0

    return PositionSize(
        pair=pair,
        qty=qty,
        notional_usd=notional,
        pct_of_capital=target_pct * 100,
        method="min(ATR-risk, frac-Kelly, cap-limit)",
    )

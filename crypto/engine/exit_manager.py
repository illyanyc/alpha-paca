"""Exit manager — ATR trailing stops, take-profit, time exits, signal exits.

Manages per-position stop levels and checks exit conditions every tick.
The ATR trailing stop is the single most impactful risk component
(+0.73 Sharpe improvement per AdaptiveTrend ablation study).
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger(__name__)


@dataclass
class PositionExit:
    pair: str
    bot_id: str
    exit_type: str
    exit_price: float
    reason: str


@dataclass
class StopState:
    """Per-position stop/target tracking."""
    entry_price: float
    initial_stop: float
    trailing_stop: float
    take_profit: float
    highest_since_entry: float
    atr_at_entry: float
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ExitManager:
    """Tracks trailing stops and checks exit conditions for all open positions."""

    def __init__(self) -> None:
        self._stops: dict[str, StopState] = {}

    def _key(self, pair: str, bot_id: str) -> str:
        return f"{pair}:{bot_id}"

    def register_position(
        self,
        pair: str,
        bot_id: str,
        entry_price: float,
        atr_value: float,
        stop_multiplier: float = 2.0,
        tp_multiplier: float = 3.0,
    ) -> StopState:
        """Set initial stop-loss and take-profit for a new position."""
        stop_distance = atr_value * stop_multiplier
        initial_stop = entry_price - stop_distance
        take_profit = entry_price + atr_value * tp_multiplier

        state = StopState(
            entry_price=entry_price,
            initial_stop=initial_stop,
            trailing_stop=initial_stop,
            take_profit=take_profit,
            highest_since_entry=entry_price,
            atr_at_entry=atr_value,
        )
        self._stops[self._key(pair, bot_id)] = state

        logger.info(
            "exit_registered",
            pair=pair, bot_id=bot_id,
            entry=f"${entry_price:,.2f}",
            stop=f"${initial_stop:,.2f}",
            tp=f"${take_profit:,.2f}",
            atr=f"${atr_value:,.2f}",
        )
        return state

    def update_trailing_stop(
        self,
        pair: str,
        bot_id: str,
        current_high: float,
        current_atr: float | None = None,
        stop_multiplier: float = 2.0,
    ) -> float | None:
        """Trail the stop upward as price makes new highs.  Never moves down."""
        key = self._key(pair, bot_id)
        state = self._stops.get(key)
        if not state:
            return None

        if current_high > state.highest_since_entry:
            state.highest_since_entry = current_high
            atr = current_atr if current_atr else state.atr_at_entry
            new_stop = current_high - atr * stop_multiplier
            if new_stop > state.trailing_stop:
                state.trailing_stop = new_stop

        return state.trailing_stop

    def remove_position(self, pair: str, bot_id: str) -> None:
        """Clean up state after position is closed."""
        self._stops.pop(self._key(pair, bot_id), None)

    def get_stop_state(self, pair: str, bot_id: str) -> StopState | None:
        return self._stops.get(self._key(pair, bot_id))

    def check_exits(
        self,
        positions: list[dict[str, Any]],
        indicators_4h: dict[str, dict[str, Any]],
        composite_scores: dict[str, float] | None = None,
        exit_threshold: int = -20,
        current_time: datetime | None = None,
    ) -> list[PositionExit]:
        """Check all exit conditions for every open position.

        Returns a list of PositionExit for positions that should be closed.
        Exit priority: ATR stop > TP > Signal exits > Time exit.
        """
        now = current_time or datetime.now(timezone.utc)
        exits: list[PositionExit] = []

        for pos in positions:
            pair = pos.get("pair", "")
            bot_id = pos.get("bot_id", "momentum")
            qty = float(pos.get("qty", 0))
            if qty <= 0 or not pair:
                continue

            current_price = float(pos.get("current_price", 0))
            if current_price <= 0:
                continue

            key = self._key(pair, bot_id)
            state = self._stops.get(key)

            if not state:
                entry_price = float(pos.get("avg_entry_price", current_price))
                ind = indicators_4h.get(pair, {})
                atr_val = ind.get("atr") if ind else None
                if atr_val and atr_val > 0:
                    state = self.register_position(
                        pair, bot_id, entry_price, atr_val,
                    )
                else:
                    continue

            ind = indicators_4h.get(pair, {})
            current_atr = ind.get("atr") if ind else None
            current_high = float(pos.get("current_price", 0))
            self.update_trailing_stop(pair, bot_id, current_high, current_atr)

            if current_price <= state.trailing_stop:
                pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
                exits.append(PositionExit(
                    pair=pair, bot_id=bot_id,
                    exit_type="trailing_stop",
                    exit_price=current_price,
                    reason=f"ATR trailing stop hit at ${state.trailing_stop:,.2f} (PnL {pnl_pct:+.1f}%)",
                ))
                continue

            if current_price >= state.take_profit:
                pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
                exits.append(PositionExit(
                    pair=pair, bot_id=bot_id,
                    exit_type="take_profit",
                    exit_price=current_price,
                    reason=f"Take profit reached at ${state.take_profit:,.2f} (+{pnl_pct:.1f}%)",
                ))
                continue

            if ind:
                macd_bearish_cross = ind.get("macd_4h_bearish_cross", False)
                if macd_bearish_cross:
                    exits.append(PositionExit(
                        pair=pair, bot_id=bot_id,
                        exit_type="signal_exit",
                        exit_price=current_price,
                        reason="MACD(8-17-9) bearish crossover on 4H",
                    ))
                    continue

                rsi_5 = ind.get("rsi_5")
                if rsi_5 is not None and rsi_5 < 40:
                    exits.append(PositionExit(
                        pair=pair, bot_id=bot_id,
                        exit_type="signal_exit",
                        exit_price=current_price,
                        reason=f"RSI(5) breakdown to {rsi_5:.0f} (< 40 threshold)",
                    ))
                    continue

            if composite_scores:
                comp = composite_scores.get(pair, 0)
                if comp < exit_threshold:
                    exits.append(PositionExit(
                        pair=pair, bot_id=bot_id,
                        exit_type="signal_exit",
                        exit_price=current_price,
                        reason=f"Composite score {comp:.0f} < {exit_threshold} exit threshold",
                    ))
                    continue

            if now.hour >= 23:
                pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
                if bot_id == "momentum":
                    exits.append(PositionExit(
                        pair=pair, bot_id=bot_id,
                        exit_type="time_exit",
                        exit_price=current_price,
                        reason=f"End-of-day time exit at 23:00 UTC (PnL {pnl_pct:+.1f}%)",
                    ))
                    continue

            entry_price = float(pos.get("avg_entry_price", 0))
            if entry_price > 0:
                hard_stop_pct = -3.0
                pnl_pct = (current_price - entry_price) / entry_price * 100
                if pnl_pct <= hard_stop_pct:
                    exits.append(PositionExit(
                        pair=pair, bot_id=bot_id,
                        exit_type="hard_stop",
                        exit_price=current_price,
                        reason=f"Hard stop at {pnl_pct:.1f}% (backup if ATR stop wider)",
                    ))

        return exits

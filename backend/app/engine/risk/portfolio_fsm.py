"""Drawdown state machine governing position sizing during drawdowns."""

from __future__ import annotations

from enum import Enum

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class DrawdownState(str, Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"
    HALTED = "HALTED"
    PANIC = "PANIC"


_POSITION_SCALE: dict[DrawdownState, float] = {
    DrawdownState.NORMAL: 1.0,
    DrawdownState.REDUCED: 0.5,
    DrawdownState.HALTED: 0.0,
    DrawdownState.PANIC: 0.0,
}


class PortfolioFSM:
    """Finite state machine that controls position scaling based on drawdown depth."""

    def __init__(self) -> None:
        settings = get_settings()
        self._reduced_pct = settings.drawdown.reduced_pct
        self._halted_pct = settings.drawdown.halted_pct
        self._panic_pct = settings.drawdown.panic_pct
        self._state = DrawdownState.NORMAL

    @property
    def state(self) -> DrawdownState:
        return self._state

    def transition(self, current_drawdown_pct: float) -> DrawdownState:
        """Determine new state from absolute drawdown percentage.

        Transitions upward immediately but requires the drawdown to recover
        fully past a threshold before stepping back down.
        """
        dd = abs(current_drawdown_pct)
        previous = self._state

        if dd >= self._panic_pct:
            self._state = DrawdownState.PANIC
        elif dd >= self._halted_pct:
            self._state = DrawdownState.HALTED
        elif dd >= self._reduced_pct:
            self._state = DrawdownState.REDUCED
        else:
            self._state = DrawdownState.NORMAL

        if self._state != previous:
            logger.warning(
                "drawdown_state_transition",
                previous=previous.value,
                new=self._state.value,
                drawdown_pct=dd,
            )

        return self._state

    @staticmethod
    def get_position_scale(state: DrawdownState) -> float:
        """Return the sizing multiplier for the given state."""
        return _POSITION_SCALE.get(state, 0.0)

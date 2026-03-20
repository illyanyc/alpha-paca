"""Position management — syncs broker positions and monitors exits."""

from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class PositionManager:
    """Keeps the local position book in sync with the broker."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._positions: list[dict[str, Any]] = []

    def sync_positions(self) -> list[dict[str, Any]]:
        """Fetch positions from Alpaca and reconcile with the local book.

        Stub — returns the current in-memory list.
        """
        logger.info("positions_synced", count=len(self._positions))
        return self._positions

    def update_pnl(
        self,
        positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Recalculate unrealised PnL for each position."""
        for pos in positions:
            entry = pos.get("entry_price", 0.0)
            current = pos.get("current_price", 0.0)
            qty = pos.get("qty", 0.0)
            side_mult = 1.0 if pos.get("side") == "long" else -1.0
            pos["unrealized_pnl"] = (current - entry) * qty * side_mult
        return positions

    def check_exits(
        self,
        positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return positions that have triggered an exit condition (stop or target)."""
        exits: list[dict[str, Any]] = []
        for pos in positions:
            current = pos.get("current_price", 0.0)
            stop = pos.get("stop_loss", 0.0)
            side = pos.get("side", "long")

            stop_hit = (
                (side == "long" and current <= stop)
                or (side == "short" and current >= stop)
            )
            if stop_hit:
                pos["exit_reason"] = "stop_loss"
                exits.append(pos)
                continue

            targets: dict[str, float] = pos.get("target_prices") or {}
            for label, target in targets.items():
                target_hit = (
                    (side == "long" and current >= target)
                    or (side == "short" and current <= target)
                )
                if target_hit:
                    pos["exit_reason"] = f"target_{label}"
                    exits.append(pos)
                    break

        if exits:
            logger.info("exit_signals", count=len(exits))
        return exits

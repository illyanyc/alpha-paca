"""Fill handling — processes completed orders and records trades."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class FillHandler:
    """Processes order fills and converts them into Trade records."""

    @staticmethod
    def handle_fill(
        order: dict[str, Any],
        fill_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge fill information into the order and return a trade record."""
        fill_price = fill_data.get("fill_price", order.get("limit_price", 0.0))
        fill_qty = fill_data.get("fill_qty", order.get("qty", 0.0))
        expected_price = order.get("limit_price", fill_price)
        slippage = FillHandler.compute_slippage(expected_price, fill_price)

        trade = FillHandler.record_trade(order, {
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "slippage_bps": slippage,
            "filled_at": fill_data.get("filled_at", datetime.now(timezone.utc).isoformat()),
        })

        logger.info(
            "fill_processed",
            symbol=order.get("symbol"),
            fill_price=fill_price,
            slippage_bps=slippage,
        )
        return trade

    @staticmethod
    def compute_slippage(
        expected_price: float,
        fill_price: float,
    ) -> float:
        """Slippage in basis points."""
        if expected_price == 0:
            return 0.0
        return abs(fill_price - expected_price) / expected_price * 10_000

    @staticmethod
    def record_trade(
        order: dict[str, Any],
        fill: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a trade record dict suitable for DB insertion."""
        return {
            "symbol": order.get("symbol"),
            "pod_name": order.get("pod_name"),
            "side": order.get("side"),
            "entry_price": fill.get("fill_price"),
            "qty": fill.get("fill_qty"),
            "slippage_entry_bps": fill.get("slippage_bps", 0.0),
            "entry_time": fill.get("filled_at"),
            "status": "open",
        }

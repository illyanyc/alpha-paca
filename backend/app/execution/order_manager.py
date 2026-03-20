"""Order lifecycle management — submit, cancel, and monitor fills via Alpaca."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.config import get_settings
from app.models.signal import PodSignalOut

logger = structlog.get_logger(__name__)


class OrderManager:
    """Translates validated signals into orders and tracks their lifecycle."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._pending_orders: dict[str, dict[str, Any]] = {}

    def submit_order(
        self,
        signal: PodSignalOut,
        position_size: float,
    ) -> dict[str, Any]:
        """Create and submit an order to the broker.

        Returns an order record dict.  In production this calls the Alpaca
        Orders API; here it returns a stub acknowledging the submission.
        """
        order_id = str(uuid.uuid4())
        order = {
            "order_id": order_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "qty": position_size,
            "order_type": "limit",
            "limit_price": signal.entry_price,
            "status": "submitted",
            "pod_name": signal.pod_name,
            "signal_id": str(signal.id) if signal.id else None,
        }
        self._pending_orders[order_id] = order
        logger.info(
            "order_submitted",
            order_id=order_id,
            symbol=signal.symbol,
            side=signal.side,
            qty=position_size,
        )
        return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Request cancellation for a pending order."""
        order = self._pending_orders.pop(order_id, None)
        if order is None:
            logger.warning("order_cancel_not_found", order_id=order_id)
            return {"order_id": order_id, "status": "not_found"}

        order["status"] = "cancelled"
        logger.info("order_cancelled", order_id=order_id)
        return order

    def monitor_fills(self) -> list[dict[str, Any]]:
        """Poll for filled orders.

        In production this queries the Alpaca API for fill events.
        """
        filled: list[dict[str, Any]] = []
        for oid, order in list(self._pending_orders.items()):
            # Stub: all orders remain pending until real broker integration.
            if order.get("status") == "filled":
                filled.append(order)
                del self._pending_orders[oid]
        return filled

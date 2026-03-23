"""Order lifecycle management — submit, cancel, and monitor fills via Alpaca."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from app.config import get_settings
from app.models.signal import PodSignalOut
from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)


class OrderManager:
    """Translates validated signals into orders and tracks their lifecycle."""

    def __init__(self, alpaca: AlpacaService | None = None) -> None:
        self._settings = get_settings()
        self._alpaca = alpaca
        self._pending_orders: dict[str, dict[str, Any]] = {}

    def is_shortable(self, symbol: str) -> bool:
        """Check if a symbol is available for short selling via Alpaca."""
        if self._alpaca is None:
            return False
        try:
            asset = self._alpaca.get_asset(symbol)
            return bool(getattr(asset, "shortable", False))
        except Exception:
            logger.warning("shortable_check_failed", symbol=symbol)
            return False

    def submit_order(
        self,
        signal: PodSignalOut,
        position_size: float,
    ) -> dict[str, Any]:
        """Create and submit an order to the broker.

        Returns an order record dict. When Alpaca is configured, submits via
        the Orders API; otherwise records a local pending order only.
        """
        order_id = str(uuid.uuid4())
        side_str = signal.side.lower()

        order: dict[str, Any] = {
            "order_id": order_id,
            "symbol": signal.symbol,
            "side": side_str,
            "qty": position_size,
            "order_type": "limit",
            "limit_price": signal.entry_price,
            "status": "submitted",
            "pod_name": signal.pod_name,
            "signal_id": str(signal.id) if signal.id else None,
            "alpaca_order_id": None,
        }

        if side_str in ("short", "sell") and self._alpaca is not None:
            if not self.is_shortable(signal.symbol):
                order["status"] = "rejected"
                order["reject_reason"] = "not_shortable"
                self._pending_orders[order_id] = order
                logger.warning("order_rejected_not_shortable", symbol=signal.symbol)
                return order

        if self._alpaca is not None and position_size > 0:
            try:
                if side_str in ("long", "buy"):
                    alpaca_side = OrderSide.BUY
                elif side_str in ("short", "sell"):
                    alpaca_side = OrderSide.SELL
                else:
                    alpaca_side = OrderSide.BUY
                alpaca_req = LimitOrderRequest(
                    symbol=signal.symbol,
                    qty=max(int(position_size), 1),
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(signal.entry_price, 2),
                )
                result = self._alpaca.submit_order(alpaca_req)
                order["alpaca_order_id"] = str(getattr(result, "id", ""))
                order["status"] = str(getattr(result, "status", "submitted")).lower()
                logger.info(
                    "alpaca_order_submitted",
                    alpaca_id=order["alpaca_order_id"],
                    symbol=signal.symbol,
                )
            except Exception as exc:
                logger.error("alpaca_order_failed", symbol=signal.symbol, error=str(exc))
                order["status"] = "rejected"
                order["reject_reason"] = str(exc)

        self._pending_orders[order_id] = order
        logger.info(
            "order_submitted",
            order_id=order_id,
            symbol=signal.symbol,
            side=side_str,
            qty=position_size,
        )
        return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Request cancellation for a pending order."""
        order = self._pending_orders.pop(order_id, None)
        if order is None:
            logger.warning("order_cancel_not_found", order_id=order_id)
            return {"order_id": order_id, "status": "not_found"}

        if self._alpaca is not None and order.get("alpaca_order_id"):
            try:
                self._alpaca.cancel_order(order["alpaca_order_id"])
            except Exception as exc:
                logger.warning(
                    "alpaca_cancel_failed", order_id=order_id, error=str(exc)
                )

        order["status"] = "cancelled"
        logger.info("order_cancelled", order_id=order_id)
        return order

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Return all pending orders."""
        return list(self._pending_orders.values())

    def get_pending_count(self) -> int:
        """Return count of pending orders."""
        return len(self._pending_orders)

    def monitor_fills(self) -> list[dict[str, Any]]:
        """Poll for filled orders."""
        filled: list[dict[str, Any]] = []
        for oid, order in list(self._pending_orders.items()):
            alpaca_id = order.get("alpaca_order_id")
            if self._alpaca is not None and alpaca_id:
                try:
                    alpaca_order = self._alpaca.get_order_by_id(alpaca_id)
                    status = str(getattr(alpaca_order, "status", "")).lower()
                    if status == "filled":
                        order["status"] = "filled"
                        order["fill_price"] = float(
                            getattr(alpaca_order, "filled_avg_price", 0) or 0
                        )
                        order["filled_qty"] = float(
                            getattr(alpaca_order, "filled_qty", 0) or 0
                        )
                        filled.append(order)
                        del self._pending_orders[oid]
                    elif status in ("cancelled", "expired", "rejected"):
                        order["status"] = status
                        del self._pending_orders[oid]
                except Exception as exc:
                    logger.warning("fill_monitor_error", order_id=oid, error=str(exc))
            elif order.get("status") == "filled":
                filled.append(order)
                del self._pending_orders[oid]
        return filled

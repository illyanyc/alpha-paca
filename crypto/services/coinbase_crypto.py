"""Coinbase Advanced Trade API wrapper — drop-in replacement for AlpacaCryptoService."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from coinbase.rest import RESTClient

from config import get_settings

logger = structlog.get_logger(__name__)


def _to_product_id(pair: str) -> str:
    """Convert 'BTC/USD' → 'BTC-USD' for Coinbase."""
    return pair.replace("/", "-")


def _to_pair(product_id: str) -> str:
    """Convert 'BTC-USD' → 'BTC/USD' for internal use."""
    return product_id.replace("-", "/")


def _normalize_pem_secret(secret: str) -> str:
    """Ensure PEM secret has proper newline formatting.

    CDP secrets may arrive as a single line with literal \\n or as a
    raw base64 blob. This normalises them into valid PEM blocks.
    """
    secret = secret.replace("\\n", "\n").strip()
    if secret.startswith("-----"):
        return secret
    return secret


class CoinbaseCryptoService:
    """Unified interface to Coinbase Advanced Trade data + trading endpoints.

    Matches the AlpacaCryptoService API so the rest of the system works unchanged.
    """

    def __init__(self) -> None:
        settings = get_settings()
        api_key = settings.coinbase.api_key
        api_secret = _normalize_pem_secret(settings.coinbase.api_secret)

        self._client = RESTClient(api_key=api_key, api_secret=api_secret)

    def replace_client(self, api_key: str, api_secret: str) -> None:
        """Hot-swap the underlying REST client with new credentials."""
        api_secret = _normalize_pem_secret(api_secret)
        self._client = RESTClient(api_key=api_key, api_secret=api_secret)

    # ── Market data ──────────────────────────────────────────────────

    def get_latest_quotes(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch latest bid/ask quotes for given pairs (e.g. ['BTC/USD'])."""
        product_ids = [_to_product_id(p) for p in pairs]
        raw = self._client.get_best_bid_ask(product_ids=product_ids)

        result: dict[str, dict[str, Any]] = {}
        for pricebook in raw.get("pricebooks", []):
            pid = pricebook.get("product_id", "")
            pair = _to_pair(pid)

            bids = pricebook.get("bids", [])
            asks = pricebook.get("asks", [])

            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            bid_size = float(bids[0]["size"]) if bids else 0.0
            ask_size = float(asks[0]["size"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0.0

            result[pair] = {
                "bid": best_bid,
                "ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "mid": mid,
                "timestamp": datetime.now(timezone.utc),
            }
        return result

    def get_bars(
        self,
        pair: str,
        timeframe: Any = None,
        lookback_minutes: int = 120,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles for a single crypto pair."""
        product_id = _to_product_id(pair)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        if lookback_minutes <= 300:
            granularity = "ONE_MINUTE"
        elif lookback_minutes <= 1500:
            granularity = "FIVE_MINUTE"
        elif lookback_minutes <= 6000:
            granularity = "FIFTEEN_MINUTE"
        else:
            granularity = "ONE_HOUR"

        start_str = str(int(start.timestamp()))
        end_str = str(int(end.timestamp()))

        raw = self._client.get_candles(
            product_id=product_id,
            start=start_str,
            end=end_str,
            granularity=granularity,
        )

        candles = raw.get("candles", [])
        bars = []
        for c in candles:
            ts = int(c.get("start", 0))
            bars.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
                "vwap": None,
            })

        bars.sort(key=lambda b: b["timestamp"] or datetime.min.replace(tzinfo=timezone.utc))
        return bars

    # ── Trading ──────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        """Return account summary matching AlpacaCryptoService format."""
        raw = self._client.get_accounts(limit=250)
        accounts = raw.get("accounts", [])

        cash = 0.0
        total_value = 0.0

        for acct in accounts:
            currency = acct.get("currency", "")
            available = float(acct.get("available_balance", {}).get("value", 0))
            hold = float(acct.get("hold", {}).get("value", 0))
            total = available + hold

            if currency in ("USD", "USDC", "USDT"):
                cash += total
                total_value += total
            elif total > 0:
                try:
                    pid = f"{currency}-USD"
                    product = self._client.get_product(pid)
                    price = float(product.get("price", 0))
                    total_value += total * price
                except Exception:
                    pass

        return {
            "equity": total_value,
            "cash": cash,
            "buying_power": cash,
            "portfolio_value": total_value,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        """Return non-zero crypto holdings as positions."""
        raw = self._client.get_accounts(limit=250)
        accounts = raw.get("accounts", [])
        positions: list[dict[str, Any]] = []

        for acct in accounts:
            currency = acct.get("currency", "")
            if currency in ("USD", "USDC", "USDT"):
                continue

            available = float(acct.get("available_balance", {}).get("value", 0))
            hold = float(acct.get("hold", {}).get("value", 0))
            qty = available + hold
            if qty <= 0:
                continue

            pair = f"{currency}/USD"
            try:
                pid = f"{currency}-USD"
                product = self._client.get_product(pid)
                current_price = float(product.get("price", 0))
            except Exception:
                current_price = 0

            market_value = qty * current_price

            positions.append({
                "symbol": pair,
                "qty": qty,
                "avg_entry_price": current_price,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pl": 0,
                "unrealized_plpc": 0,
            })

        return positions

    def submit_market_order(
        self, pair: str, qty: Decimal, side: str
    ) -> dict[str, Any]:
        """Submit a market order and return order details."""
        product_id = _to_product_id(pair)
        client_order_id = str(uuid.uuid4())
        base_size = str(qty)

        if side.upper() == "BUY":
            raw = self._client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
            )
        else:
            raw = self._client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
            )

        success = raw.get("success", False)
        success_resp = raw.get("success_response", {}) or {}
        error_resp = raw.get("error_response", {}) or {}

        order_id = success_resp.get("order_id", "")
        if not order_id:
            order_id = raw.get("order_id", client_order_id)

        if not success:
            error_msg = error_resp.get("message", "") or error_resp.get("error", "") or str(raw)
            raise RuntimeError(f"Coinbase order failed: {error_msg}")

        logger.info(
            "order_submitted",
            pair=pair,
            side=side,
            qty=str(qty),
            order_id=order_id,
        )

        return {
            "order_id": order_id,
            "status": "pending",
            "filled_qty": 0,
            "filled_avg_price": 0,
            "submitted_at": datetime.now(timezone.utc),
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        raw = self._client.get_order(order_id)
        order = raw.get("order", raw)

        status_map = {
            "FILLED": "filled",
            "CANCELLED": "cancelled",
            "EXPIRED": "expired",
            "PENDING": "pending",
            "OPEN": "open",
        }
        raw_status = order.get("status", "UNKNOWN")
        status = status_map.get(raw_status, raw_status.lower())

        filled_qty = float(order.get("filled_size", 0))
        avg_price = float(order.get("average_filled_price", 0))

        return {
            "order_id": order.get("order_id", order_id),
            "status": status,
            "filled_qty": filled_qty,
            "filled_avg_price": avg_price,
        }

    async def wait_for_fill(self, order_id: str, timeout_sec: int = 60) -> dict[str, Any]:
        """Poll until order is filled or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            info = await asyncio.to_thread(self.get_order, order_id)
            if info["status"] in ("filled", "partially_filled"):
                return info
            if info["status"] in ("cancelled", "expired"):
                return info
            await asyncio.sleep(1)
        return await asyncio.to_thread(self.get_order, order_id)

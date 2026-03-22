"""Coinbase Advanced Trade WebSocket client for real-time order book and trade data.

Connects to the public (no auth) market data endpoint for level2 and market_trades.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

WS_URL = "wss://advanced-trade-ws.coinbase.com"


def _to_product_id(pair: str) -> str:
    return pair.replace("/", "-")


class CoinbaseWebSocket:
    """Async WebSocket client for Coinbase market data."""

    def __init__(self, pairs: list[str], on_book_update=None, on_trade=None) -> None:
        self.pairs = pairs
        self.product_ids = [_to_product_id(p) for p in pairs]
        self._on_book_update = on_book_update
        self._on_trade = on_trade
        self._ws = None
        self._running = False
        self._reconnect_delay = 2

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                if not self._running:
                    break
                logger.warning("ws_reconnecting", error=str(e)[:100], delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _connect_and_listen(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.warning("websockets_not_installed", hint="pip install websockets")
            await asyncio.sleep(60)
            return

        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 2
            logger.info("ws_connected", url=WS_URL)

            subscribe_msg = {
                "type": "subscribe",
                "product_ids": self.product_ids,
                "channel": "level2",
            }
            await ws.send(json.dumps(subscribe_msg))

            trade_msg = {
                "type": "subscribe",
                "product_ids": self.product_ids,
                "channel": "market_trades",
            }
            await ws.send(json.dumps(trade_msg))

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    await self._handle_message(msg)
                except (json.JSONDecodeError, KeyError):
                    continue

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel", "")
        events = msg.get("events", [])

        if channel == "l2_data" and self._on_book_update:
            for event in events:
                product_id = event.get("product_id", "")
                pair = product_id.replace("-", "/")
                updates = event.get("updates", [])

                bids = []
                asks = []
                for u in updates:
                    side = u.get("side", "")
                    price = float(u.get("price_level", 0))
                    qty = float(u.get("new_quantity", 0))
                    if side == "bid":
                        bids.append((price, qty))
                    elif side == "offer":
                        asks.append((price, qty))

                if bids or asks:
                    await self._on_book_update(pair, bids, asks)

        elif channel == "market_trades" and self._on_trade:
            for event in events:
                trades = event.get("trades", [])
                for t in trades:
                    product_id = t.get("product_id", "")
                    pair = product_id.replace("-", "/")
                    price = float(t.get("price", 0))
                    size = float(t.get("size", 0))
                    side = t.get("side", "").lower()
                    if side in ("buy", "sell"):
                        await self._on_trade(pair, price, size, side)

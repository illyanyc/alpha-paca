"""Alpaca crypto API wrapper — historical data, trading, and WebSocket price stream."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from config import get_settings

logger = structlog.get_logger(__name__)


class AlpacaCryptoService:
    """Unified interface to Alpaca's crypto data + trading endpoints."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = settings.alpaca.api_key
        api_secret = settings.alpaca.api_secret
        paper = settings.alpaca.paper

        # Crypto market data is available without auth; use keys only if valid
        self._data_client = CryptoHistoricalDataClient()
        self._trading_client = TradingClient(
            api_key=api_key, secret_key=api_secret, paper=paper
        )

    # ── Market data ──────────────────────────────────────────────────

    def get_latest_quotes(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch latest bid/ask quotes for given pairs (e.g. ['BTC/USD'])."""
        req = CryptoLatestQuoteRequest(symbol_or_symbols=pairs)
        raw = self._data_client.get_crypto_latest_quote(req)
        result: dict[str, dict[str, Any]] = {}
        for symbol, quote in raw.items():
            result[symbol] = {
                "bid": float(quote.bid_price),
                "ask": float(quote.ask_price),
                "bid_size": float(quote.bid_size),
                "ask_size": float(quote.ask_size),
                "mid": (float(quote.bid_price) + float(quote.ask_price)) / 2,
                "timestamp": quote.timestamp,
            }
        return result

    def get_bars(
        self,
        pair: str,
        timeframe: TimeFrame = TimeFrame.Minute,
        lookback_minutes: int = 120,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV bars for a single crypto pair."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)
        req = CryptoBarsRequest(
            symbol_or_symbols=[pair],
            timeframe=timeframe,
            start=start,
            end=end,
        )
        bars_set = self._data_client.get_crypto_bars(req)
        try:
            bars = bars_set[pair]
        except (KeyError, IndexError):
            bars = []
        return [
            {
                "timestamp": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "vwap": float(b.vwap) if b.vwap else None,
            }
            for b in bars
        ]

    # ── Trading ──────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        acct = self._trading_client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        positions = self._trading_client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
            if "/" in (p.symbol or "")  # crypto pairs contain "/"
        ]

    def submit_market_order(
        self, pair: str, qty: Decimal, side: str
    ) -> dict[str, Any]:
        """Submit a market order and return order details."""
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=pair,
            qty=float(qty),
            side=order_side,
            time_in_force=TimeInForce.GTC,
        )
        order = self._trading_client.submit_order(req)
        logger.info(
            "order_submitted",
            pair=pair,
            side=side,
            qty=str(qty),
            order_id=str(order.id),
        )
        return {
            "order_id": str(order.id),
            "status": str(order.status),
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else 0,
            "submitted_at": order.submitted_at,
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        order = self._trading_client.get_order_by_id(order_id)
        return {
            "order_id": str(order.id),
            "status": str(order.status),
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else 0,
        }

    async def wait_for_fill(self, order_id: str, timeout_sec: int = 60) -> dict[str, Any]:
        """Poll until order is filled or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            info = self.get_order(order_id)
            if info["status"] in ("filled", "partially_filled"):
                return info
            await asyncio.sleep(1)
        return self.get_order(order_id)

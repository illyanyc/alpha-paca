"""Alpaca broker integration via the official alpaca-py SDK."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest, OrderRequest

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def _parse_timeframe(timeframe: TimeFrame | str) -> TimeFrame:
    if isinstance(timeframe, TimeFrame):
        return timeframe
    s = str(timeframe).strip().lower()
    if s in ("1day", "day", "d", "1d"):
        return TimeFrame.Day
    if s in ("1hour", "hour", "h", "1h"):
        return TimeFrame.Hour
    if s in ("1week", "week", "w", "1w"):
        return TimeFrame.Week
    if s in ("1month", "month", "m", "1m"):
        return TimeFrame.Month
    if s in ("1min", "minute", "min"):
        return TimeFrame.Minute
    raise ValueError(f"Unsupported timeframe: {timeframe!r}")


def _read_max_tradable(hot_config: Any) -> float:
    if hot_config is None:
        return 0.0
    if isinstance(hot_config, dict):
        raw = hot_config.get("capital.max_tradable", 0.0)
    else:
        getter = getattr(hot_config, "get", None)
        if not callable(getter):
            return 0.0
        raw = getter("capital.max_tradable")
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


class AlpacaService:
    """Thin async-friendly facade over Alpaca trading and market-data clients."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        paper = self._settings.alpaca.paper
        key = self._settings.alpaca.api_key
        secret = self._settings.alpaca.api_secret
        self._trading = TradingClient(api_key=key, secret_key=secret, paper=paper)
        self._data = StockHistoricalDataClient(api_key=key, secret_key=secret)
        logger.info("alpaca_service_init", paper=paper)

    def get_account(self) -> Any:
        return self._trading.get_account()

    def get_positions(self) -> list[Any]:
        return self._trading.get_all_positions()

    def submit_order(self, order_data: OrderRequest) -> Any:
        return self._trading.submit_order(order_data)

    def cancel_order(self, order_id: str) -> Any:
        return self._trading.cancel_order_by_id(order_id)

    def get_asset(self, symbol: str) -> Any:
        return self._trading.get_asset(symbol)

    def get_order_by_id(self, order_id: str) -> Any:
        return self._trading.get_order_by_id(order_id)

    def get_clock(self) -> Any:
        return self._trading.get_clock()

    def get_calendar(self, start: date, end: date) -> Any:
        return self._trading.get_calendar(GetCalendarRequest(start=start, end=end))

    def get_bars(
        self,
        symbol: str,
        timeframe: TimeFrame | str,
        start: datetime,
        end: datetime,
    ) -> Any:
        tf = _parse_timeframe(timeframe)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
        )
        return self._data.get_stock_bars(req)

    def get_latest_trade_price(self, symbol: str) -> float:
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        data = self._data.get_stock_latest_trade(req)
        if isinstance(data, dict):
            trade = data.get(symbol)
            if trade is not None and getattr(trade, "price", None) is not None:
                return float(trade.price)
        raise ValueError(f"No latest trade price for {symbol!r}")

    def get_effective_nav(self, hot_config: Any | None = None) -> float:
        account = self.get_account()
        raw_eq = getattr(account, "equity", None) or getattr(account, "portfolio_value", None)
        equity = float(raw_eq) if raw_eq is not None else 0.0
        cap = _read_max_tradable(hot_config)
        if cap > 0:
            return min(equity, cap)
        return equity

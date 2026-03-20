"""US equity session helpers via Alpaca clock and calendar APIs."""

from __future__ import annotations

from datetime import date, datetime

import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)


class MarketCalendar:
    def __init__(self, alpaca: AlpacaService | None = None) -> None:
        self._alpaca = alpaca or AlpacaService()

    def is_market_open(self) -> bool:
        clock = self._alpaca.get_clock()
        return bool(getattr(clock, "is_open", False))

    def get_next_open(self) -> datetime:
        clock = self._alpaca.get_clock()
        return getattr(clock, "next_open")

    def get_next_close(self) -> datetime:
        clock = self._alpaca.get_clock()
        return getattr(clock, "next_close")

    def is_trading_day(self, d: date) -> bool:
        trading = self._alpaca.get_calendar(d, d)
        if isinstance(trading, list) and trading:
            return True
        if isinstance(trading, list):
            return False
        logger.warning("unexpected_calendar_response", trading_type=type(trading).__name__)
        return False

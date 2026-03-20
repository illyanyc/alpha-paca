"""Combine Alpaca market data with FMP fundamentals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from alpaca.data.timeframe import TimeFrame

from app.services.alpaca_client import AlpacaService
from app.services.fmp_client import FMPClient

logger = structlog.get_logger(__name__)


class MarketDataService:
    def __init__(
        self,
        alpaca: AlpacaService | None = None,
        fmp: FMPClient | None = None,
    ) -> None:
        self._alpaca = alpaca or AlpacaService()
        self._fmp = fmp or FMPClient()

    def get_latest_price(self, symbol: str) -> float:
        return self._alpaca.get_latest_trade_price(symbol)

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: TimeFrame | str,
        days: int,
    ) -> Any:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(days, 1))
        return self._alpaca.get_bars(symbol, timeframe, start, end)

    async def get_fundamentals(self, symbol: str) -> dict[str, Any]:
        profile, ratios = await self._fmp.get_company_profile(symbol), await self._fmp.get_financial_ratios(
            symbol
        )
        return {"profile": profile, "ratios": ratios}

    async def get_sector_performance(self) -> Any:
        return await self._fmp.get_sector_performance()

"""Financial Modeling Prep (FMP) REST client — `/stable/` API surface."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

FMP_BASE = "https://financialmodelingprep.com"
RATE_SLEEP_SEC = 0.2


class FMPClient:
    """Async httpx wrapper for FMP stable endpoints."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._api_key = self._settings.api_keys.fmp_api_key
        self._base = FMP_BASE.rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await asyncio.sleep(RATE_SLEEP_SEC)
        q = dict(params or {})
        q["apikey"] = self._api_key
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(url, params=q)
            resp.raise_for_status()
            return resp.json()

    async def get_quote(self, symbol: str) -> Any:
        return await self._get("/stable/quote", {"symbol": symbol.upper()})

    async def get_sector_performance(self) -> Any:
        return await self._get("/stable/sector-performance-snapshot", {})

    async def get_earnings_calendar(self, from_date: date, to_date: date) -> Any:
        return await self._get(
            "/stable/earning_calendar",
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
            },
        )

    async def get_financial_ratios(self, symbol: str) -> Any:
        return await self._get("/stable/ratios", {"symbol": symbol.upper()})

    async def get_company_profile(self, symbol: str) -> Any:
        return await self._get("/stable/profile", {"symbol": symbol.upper()})

    async def company_screener(self, params: dict[str, Any]) -> Any:
        """Generic `/stable/company-screener` query (universe building)."""
        return await self._get("/stable/company-screener", params)

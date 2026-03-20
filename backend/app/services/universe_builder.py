"""Build a tradeable equity universe using FMP screening data."""

from __future__ import annotations

from typing import Any

import structlog

from app.services.fmp_client import FMPClient

logger = structlog.get_logger(__name__)


class UniverseBuilder:
    """Screen liquid US equities via FMP `company-screener` (stable API)."""

    def __init__(self, fmp: FMPClient | None = None) -> None:
        self._fmp = fmp or FMPClient()

    async def build_universe(
        self,
        *,
        min_avg_volume: int = 500_000,
        min_market_cap: int = 300_000_000,
        limit: int = 500,
        country: str = "US",
    ) -> list[dict[str, Any]]:
        """Return screener rows meeting liquidity / size / listing criteria."""
        params: dict[str, Any] = {
            "marketCapMoreThan": min_market_cap,
            "volumeMoreThan": min_avg_volume,
            "limit": limit,
            "country": country,
            "isEtf": "false",
            "isFund": "false",
        }
        data = await self._fmp.company_screener(params)
        if not isinstance(data, list):
            logger.warning("universe_screener_unexpected_shape", got=type(data).__name__)
            return []
        logger.info("universe_built", count=len(data))
        return data

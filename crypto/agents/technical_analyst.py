"""TechnicalAnalystAgent — computes indicators on 30-sec ticks, generates signals."""

from __future__ import annotations

import json

import redis.asyncio as aioredis
import structlog

from agents.base import BaseAgent
from config import get_settings
from engine.indicators import compute_all
from engine.signals import ComponentSignal, classify_technical
from services.alpaca_crypto import AlpacaCryptoService

logger = structlog.get_logger(__name__)

TECH_SIGNAL_KEY = "crypto:signals:technical"
TECH_SIGNAL_TTL = 120


class TechnicalAnalystAgent(BaseAgent):
    name = "technical_analyst"

    def __init__(self, alpaca: AlpacaCryptoService) -> None:
        super().__init__()
        self._alpaca = alpaca

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        pairs = settings.crypto.pair_list
        results: dict[str, dict] = {}

        for pair in pairs:
            try:
                bars = self._alpaca.get_bars(pair, lookback_minutes=120)
                if not bars:
                    logger.warning("no_bars", pair=pair)
                    continue

                indicators = compute_all(bars)
                signal = classify_technical(indicators)

                results[pair] = {
                    "signal": signal.signal.value,
                    "score": signal.score,
                    "confidence": signal.confidence,
                    "details": signal.details,
                    "indicators": indicators,
                }
            except Exception:
                logger.exception("technical_analysis_failed", pair=pair)

        r = await self._get_redis()
        await r.set(TECH_SIGNAL_KEY, json.dumps(results), ex=TECH_SIGNAL_TTL)

        logger.info("technical_analysis_complete", pairs=len(results))
        return results

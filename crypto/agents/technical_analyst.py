"""TechnicalAnalystAgent — computes indicators on 30-sec ticks, generates signals."""

from __future__ import annotations

import json

import redis.asyncio as aioredis
import structlog

from agents.base import BaseAgent
from config import get_settings
from engine.indicators import compute_all
from engine.signals import ComponentSignal, classify_technical
from services.coinbase_crypto import CoinbaseCryptoService

logger = structlog.get_logger(__name__)

TECH_SIGNAL_KEY = "crypto:signals:technical"
TECH_SIGNAL_TTL = 120


class TechnicalAnalystAgent(BaseAgent):
    name = "technical_analyst"

    def __init__(self, exchange: CoinbaseCryptoService) -> None:
        super().__init__()
        self._exchange = exchange

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        pairs = settings.crypto.pair_list
        results: dict[str, dict] = {}

        self.think(f"Computing indicators for {len(pairs)} pairs...")

        for pair in pairs:
            try:
                bars = self._exchange.get_bars(pair, lookback_minutes=120)
                if not bars:
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

        signals_summary = ", ".join(f"{p}: {d['signal']}({d['score']:.1f})" for p, d in results.items())
        self.think(f"Tech: {signals_summary}")

        r = await self._get_redis()
        await r.set(TECH_SIGNAL_KEY, json.dumps(results), ex=TECH_SIGNAL_TTL)

        logger.info("technical_analysis_complete", pairs=len(results))
        return results

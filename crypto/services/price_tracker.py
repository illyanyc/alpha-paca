"""Real-time crypto price tracker using exchange quotes + Redis cache."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import redis.asyncio as aioredis
import structlog

from config import get_settings
from services.coinbase_crypto import CoinbaseCryptoService

logger = structlog.get_logger(__name__)

PRICE_CACHE_KEY = "crypto:prices"
PRICE_TTL_SEC = 120


class PriceTracker:
    """Polls the exchange for latest quotes every tick and pushes to Redis."""

    def __init__(self, exchange: CoinbaseCryptoService) -> None:
        self._exchange = exchange
        settings = get_settings()
        self._pairs = settings.crypto.pair_list
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(settings.database.redis_url, decode_responses=True)
        return self._redis

    async def fetch_and_cache(self) -> dict[str, dict]:
        """Fetch latest prices and store in Redis hash. Returns the price data."""
        try:
            quotes = self._exchange.get_latest_quotes(self._pairs)
        except Exception:
            logger.exception("price_fetch_failed")
            return {}

        r = await self._get_redis()
        now = datetime.now(timezone.utc).isoformat()

        result: dict[str, dict] = {}
        for pair, q in quotes.items():
            entry = {
                "bid": q["bid"],
                "ask": q["ask"],
                "mid": q["mid"],
                "timestamp": now,
            }
            await r.hset(PRICE_CACHE_KEY, pair, json.dumps(entry))
            result[pair] = entry

        await r.expire(PRICE_CACHE_KEY, PRICE_TTL_SEC)
        logger.debug("prices_cached", count=len(result))
        return result

    async def get_cached_price(self, pair: str) -> dict | None:
        r = await self._get_redis()
        raw = await r.hget(PRICE_CACHE_KEY, pair)
        if raw:
            return json.loads(raw)
        return None

    async def get_all_cached_prices(self) -> dict[str, dict]:
        r = await self._get_redis()
        raw = await r.hgetall(PRICE_CACHE_KEY)
        return {k: json.loads(v) for k, v in raw.items()}

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

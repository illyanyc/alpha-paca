"""Redis-backed settings store for hot-swappable config that survives redeploys."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

REDIS_KEY_PREFIX = "alphapaca:crypto:settings:"
COINBASE_KEYS_KEY = f"{REDIS_KEY_PREFIX}coinbase_keys"
TRADING_SETTINGS_KEY = f"{REDIS_KEY_PREFIX}trading"
AGENT_LOG_KEY = f"{REDIS_KEY_PREFIX}agent_log"

_redis: aioredis.Redis | None = None


def init_store(redis_conn: aioredis.Redis) -> None:
    global _redis
    _redis = redis_conn


def get_redis() -> aioredis.Redis | None:
    return _redis


async def save_coinbase_keys(api_key: str, api_secret: str) -> None:
    if not _redis:
        return
    data = json.dumps({"api_key": api_key, "api_secret": api_secret})
    await _redis.set(COINBASE_KEYS_KEY, data)
    logger.info("coinbase_keys_saved_to_redis")


async def load_coinbase_keys() -> dict[str, Any] | None:
    if not _redis:
        return None
    raw = await _redis.get(COINBASE_KEYS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        logger.info("coinbase_keys_loaded_from_redis")
        return data
    except (json.JSONDecodeError, TypeError):
        return None


async def save_trading_settings(settings: dict[str, Any]) -> None:
    if not _redis:
        return
    await _redis.set(TRADING_SETTINGS_KEY, json.dumps(settings))
    logger.info("trading_settings_saved_to_redis", keys=list(settings.keys()))


async def load_trading_settings() -> dict[str, Any] | None:
    if not _redis:
        return None
    raw = await _redis.get(TRADING_SETTINGS_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def save_agent_log(log: list[dict]) -> None:
    """Persist last 50 agent thinking entries to Redis (TTL 24h)."""
    if not _redis:
        return
    await _redis.set(AGENT_LOG_KEY, json.dumps(log[-50:]), ex=86400)


async def load_agent_log() -> list[dict]:
    """Load agent thinking log from Redis on startup."""
    if not _redis:
        return []
    raw = await _redis.get(AGENT_LOG_KEY)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

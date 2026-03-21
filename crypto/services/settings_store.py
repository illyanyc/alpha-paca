"""Redis-backed settings store for hot-swappable config that survives redeploys."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

REDIS_KEY_PREFIX = "alphapaca:crypto:settings:"
COINBASE_KEYS_KEY = f"{REDIS_KEY_PREFIX}coinbase_keys"
TRADING_SETTINGS_KEY = f"{REDIS_KEY_PREFIX}trading"
AGENT_LOG_KEY = f"{REDIS_KEY_PREFIX}agent_log"
PNL_TOTAL_KEY = f"{REDIS_KEY_PREFIX}pnl:total"
PNL_DAILY_KEY_PREFIX = f"{REDIS_KEY_PREFIX}pnl:daily:"
PNL_PER_PAIR_KEY = f"{REDIS_KEY_PREFIX}pnl:pairs"

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


# ── P&L Tracker ────────────────────────────────────────────────────


def _today_key() -> str:
    return PNL_DAILY_KEY_PREFIX + datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def record_realized_pnl(
    pair: str, pnl: float, pnl_pct: float, side: str,
) -> None:
    """Record a closed trade's realized P&L in Redis (total + daily + per-pair)."""
    if not _redis:
        return
    is_win = 1 if pnl > 0 else 0

    pipe = _redis.pipeline()

    pipe.hincrbyfloat(PNL_TOTAL_KEY, "realized_pnl", pnl)
    pipe.hincrby(PNL_TOTAL_KEY, "trade_count", 1)
    pipe.hincrby(PNL_TOTAL_KEY, "win_count", is_win)

    daily_key = _today_key()
    pipe.hincrbyfloat(daily_key, "realized_pnl", pnl)
    pipe.hincrby(daily_key, "trade_count", 1)
    pipe.hincrby(daily_key, "win_count", is_win)
    pipe.expire(daily_key, 7 * 86400)

    pipe.hincrbyfloat(PNL_PER_PAIR_KEY, f"{pair}:pnl", pnl)
    pipe.hincrby(PNL_PER_PAIR_KEY, f"{pair}:trades", 1)
    pipe.hincrby(PNL_PER_PAIR_KEY, f"{pair}:wins", is_win)

    await pipe.execute()
    logger.debug("pnl_recorded", pair=pair, pnl=round(pnl, 4), side=side)


async def load_pnl_summary() -> dict[str, Any]:
    """Load total + today's P&L from Redis."""
    if not _redis:
        return _empty_pnl()

    pipe = _redis.pipeline()
    pipe.hgetall(PNL_TOTAL_KEY)
    pipe.hgetall(_today_key())
    pipe.hgetall(PNL_PER_PAIR_KEY)
    results = await pipe.execute()

    total_raw = results[0] or {}
    daily_raw = results[1] or {}
    pair_raw = results[2] or {}

    total_pnl = float(total_raw.get(b"realized_pnl", total_raw.get("realized_pnl", 0)))
    total_trades = int(total_raw.get(b"trade_count", total_raw.get("trade_count", 0)))
    total_wins = int(total_raw.get(b"win_count", total_raw.get("win_count", 0)))

    daily_pnl = float(daily_raw.get(b"realized_pnl", daily_raw.get("realized_pnl", 0)))
    daily_trades = int(daily_raw.get(b"trade_count", daily_raw.get("trade_count", 0)))
    daily_wins = int(daily_raw.get(b"win_count", daily_raw.get("win_count", 0)))

    pairs: dict[str, dict] = {}
    for key, val in pair_raw.items():
        k = key.decode() if isinstance(key, bytes) else key
        v = val.decode() if isinstance(val, bytes) else val
        pair_name, field = k.rsplit(":", 1)
        if pair_name not in pairs:
            pairs[pair_name] = {"pnl": 0.0, "trades": 0, "wins": 0}
        if field == "pnl":
            pairs[pair_name]["pnl"] = float(v)
        elif field == "trades":
            pairs[pair_name]["trades"] = int(v)
        elif field == "wins":
            pairs[pair_name]["wins"] = int(v)

    return {
        "total_realized_pnl": round(total_pnl, 4),
        "total_trades": total_trades,
        "total_win_rate": round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0,
        "daily_realized_pnl": round(daily_pnl, 4),
        "daily_trades": daily_trades,
        "daily_win_rate": round(daily_wins / daily_trades * 100, 1) if daily_trades > 0 else 0,
        "per_pair": pairs,
    }


def _empty_pnl() -> dict[str, Any]:
    return {
        "total_realized_pnl": 0.0,
        "total_trades": 0,
        "total_win_rate": 0.0,
        "daily_realized_pnl": 0.0,
        "daily_trades": 0,
        "daily_win_rate": 0.0,
        "per_pair": {},
    }

"""Hot-reloadable tunable parameters (backed by DB + optional Redis cache)."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis_async
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Setting

logger = structlog.get_logger(__name__)

REDIS_SNAPSHOT_KEY = "alphapaca:hot_config:snapshot"

# Metadata for dashboard/API: key -> descriptor. Values are merged with runtime state in HotConfig.
TUNABLE_SETTINGS: dict[str, dict[str, Any]] = {
    "capital.max_tradable": {
        "default": 0.0,
        "type": "float",
        "env_var": "CAPITAL_MAX_TRADABLE",
        "category": "capital",
        "label": "Max tradable capital (USD)",
        "description": "Cap deployable capital; 0 means use full account equity.",
        "min": 0.0,
        "max": 100_000.0,
        "step": 100.0,
    },
    "capital.reserve_cash_pct": {
        "default": 5.0,
        "type": "float",
        "env_var": "CAPITAL_RESERVE_CASH_PCT",
        "category": "capital",
        "label": "Reserve cash %",
        "description": "Fraction of NAV to keep in cash.",
        "min": 0.0,
        "max": 50.0,
        "step": 0.5,
    },
    "pods.momentum": {
        "default": 25,
        "type": "int",
        "env_var": "POD_ALLOC_MOMENTUM",
        "category": "pods",
        "label": "Momentum pod allocation %",
        "description": "Target capital weight for the momentum pod.",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "pods.mean_reversion": {
        "default": 20,
        "type": "int",
        "env_var": "POD_ALLOC_MEAN_REVERSION",
        "category": "pods",
        "label": "Mean reversion pod allocation %",
        "description": "Target capital weight for mean reversion.",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "pods.event_driven": {
        "default": 25,
        "type": "int",
        "env_var": "POD_ALLOC_EVENT_DRIVEN",
        "category": "pods",
        "label": "Event-driven pod allocation %",
        "description": "Target capital weight for event-driven strategies.",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "pods.sector_rotation": {
        "default": 20,
        "type": "int",
        "env_var": "POD_ALLOC_SECTOR_ROTATION",
        "category": "pods",
        "label": "Sector rotation pod allocation %",
        "description": "Target capital weight for sector rotation.",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "pods.stat_arb": {
        "default": 0,
        "type": "int",
        "env_var": "POD_ALLOC_STAT_ARB",
        "category": "pods",
        "label": "Stat arb pod allocation %",
        "description": "Target capital weight for statistical arbitrage.",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "risk.target_market_beta": {
        "default": 0.3,
        "type": "float",
        "env_var": "RISK_TARGET_MARKET_BETA",
        "category": "risk",
        "label": "Target market beta",
        "description": "Desired portfolio beta vs SPY.",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
    },
    "risk.max_factor_exposure": {
        "default": 0.3,
        "type": "float",
        "env_var": "RISK_MAX_FACTOR_EXPOSURE",
        "category": "risk",
        "label": "Max factor exposure",
        "description": "Absolute cap on single-factor exposure.",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
    },
    "risk.max_daily_var_pct": {
        "default": 2.0,
        "type": "float",
        "env_var": "RISK_MAX_DAILY_VAR_PCT",
        "category": "risk",
        "label": "Max daily VaR %",
        "description": "95% 1-day VaR limit as % of NAV.",
        "min": 0.5,
        "max": 10.0,
        "step": 0.5,
    },
    "risk.max_stress_loss_pct": {
        "default": 10.0,
        "type": "float",
        "env_var": "RISK_MAX_STRESS_LOSS_PCT",
        "category": "risk",
        "label": "Max stress loss %",
        "description": "Worst-case stress scenario loss cap.",
        "min": 1.0,
        "max": 30.0,
        "step": 1.0,
    },
    "risk.max_gross_exposure_pct": {
        "default": 120.0,
        "type": "float",
        "env_var": "RISK_MAX_GROSS_EXPOSURE_PCT",
        "category": "risk",
        "label": "Max gross exposure %",
        "description": "Gross notional / NAV ceiling.",
        "min": 50.0,
        "max": 200.0,
        "step": 5.0,
    },
    "risk.max_net_exposure_pct": {
        "default": 80.0,
        "type": "float",
        "env_var": "RISK_MAX_NET_EXPOSURE_PCT",
        "category": "risk",
        "label": "Max net exposure %",
        "description": "Net exposure / NAV ceiling.",
        "min": 10.0,
        "max": 150.0,
        "step": 5.0,
    },
    "risk.max_pod_return_corr": {
        "default": 0.60,
        "type": "float",
        "env_var": "RISK_MAX_POD_RETURN_CORR",
        "category": "risk",
        "label": "Max pod return correlation",
        "description": "Correlation threshold between pod returns.",
        "min": 0.1,
        "max": 1.0,
        "step": 0.05,
    },
    "position_sizing.risk_per_trade_pct": {
        "default": 1.0,
        "type": "float",
        "env_var": "POS_RISK_PER_TRADE_PCT",
        "category": "position_sizing",
        "label": "Risk per trade %",
        "description": "Target risk budget per position as % of NAV.",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
    },
    "position_sizing.max_position_pct": {
        "default": 5.0,
        "type": "float",
        "env_var": "POS_MAX_POSITION_PCT",
        "category": "position_sizing",
        "label": "Max position %",
        "description": "Maximum single-name weight.",
        "min": 1.0,
        "max": 25.0,
        "step": 1.0,
    },
    "position_sizing.max_concurrent_positions": {
        "default": 12,
        "type": "int",
        "env_var": "POS_MAX_CONCURRENT_POSITIONS",
        "category": "position_sizing",
        "label": "Max concurrent positions",
        "description": "Portfolio-wide open position cap.",
        "min": 1,
        "max": 50,
        "step": 1,
    },
    "position_sizing.max_positions_per_pod": {
        "default": 4,
        "type": "int",
        "env_var": "POS_MAX_POSITIONS_PER_POD",
        "category": "position_sizing",
        "label": "Max positions per pod",
        "description": "Per-pod concurrent position cap.",
        "min": 1,
        "max": 20,
        "step": 1,
    },
    "drawdown.reduced_pct": {
        "default": 1.5,
        "type": "float",
        "env_var": "DD_REDUCED_PCT",
        "category": "drawdown",
        "label": "Reduced risk drawdown %",
        "description": "Intraday DD threshold to reduce risk.",
        "min": 0.5,
        "max": 5.0,
        "step": 0.5,
    },
    "drawdown.halted_pct": {
        "default": 3.0,
        "type": "float",
        "env_var": "DD_HALTED_PCT",
        "category": "drawdown",
        "label": "Halted trading drawdown %",
        "description": "DD threshold to halt new risk.",
        "min": 1.0,
        "max": 10.0,
        "step": 0.5,
    },
    "drawdown.panic_pct": {
        "default": 5.0,
        "type": "float",
        "env_var": "DD_PANIC_PCT",
        "category": "drawdown",
        "label": "Panic / flatten drawdown %",
        "description": "DD threshold for emergency de-risk.",
        "min": 2.0,
        "max": 15.0,
        "step": 0.5,
    },
    "execution.fill_timeout_sec": {
        "default": 300,
        "type": "int",
        "env_var": "EXEC_FILL_TIMEOUT_SEC",
        "category": "execution",
        "label": "Fill timeout (seconds)",
        "description": "Max time to wait for working orders.",
        "min": 30,
        "max": 1800,
        "step": 30,
    },
    "execution.max_fill_retries": {
        "default": 2,
        "type": "int",
        "env_var": "EXEC_MAX_FILL_RETRIES",
        "category": "execution",
        "label": "Max fill retries",
        "description": "Retries after partial or missing fills.",
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "pre_trade.min_avg_volume": {
        "default": 500_000,
        "type": "int",
        "env_var": "PRETRADE_MIN_AVG_VOLUME",
        "category": "pre_trade",
        "label": "Min average volume",
        "description": "20-day average share volume floor.",
        "min": 10_000,
        "max": 5_000_000,
        "step": 50_000,
    },
    "pre_trade.max_spread_pct": {
        "default": 0.10,
        "type": "float",
        "env_var": "PRETRADE_MAX_SPREAD_PCT",
        "category": "pre_trade",
        "label": "Max bid-ask spread %",
        "description": "Reject names wider than this spread.",
        "min": 0.01,
        "max": 1.0,
        "step": 0.01,
    },
    "signal_qualification.min_signal_ic": {
        "default": 0.03,
        "type": "float",
        "env_var": "SIG_MIN_SIGNAL_IC",
        "category": "signal_qualification",
        "label": "Min rolling signal IC",
        "description": "Minimum information coefficient to trade a signal.",
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
    },
    "signal_qualification.min_oos_winrate": {
        "default": 52,
        "type": "int",
        "env_var": "SIG_MIN_OOS_WINRATE",
        "category": "signal_qualification",
        "label": "Min OOS win rate %",
        "description": "Out-of-sample hit rate gate.",
        "min": 40,
        "max": 80,
        "step": 1,
    },
    "signal_qualification.min_oos_profit_factor": {
        "default": 1.3,
        "type": "float",
        "env_var": "SIG_MIN_OOS_PROFIT_FACTOR",
        "category": "signal_qualification",
        "label": "Min OOS profit factor",
        "description": "Gross wins / gross losses in OOS tests.",
        "min": 1.0,
        "max": 5.0,
        "step": 0.1,
    },
}


def _coerce_value(key: str, raw: Any) -> Any:
    meta = TUNABLE_SETTINGS[key]
    t = meta["type"]
    if t == "float":
        return float(raw)
    if t == "int":
        return int(round(float(raw)))
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("1", "true", "yes", "on")
    raise ValueError(f"Unsupported tunable type: {t!r}")


def _clamp_value(key: str, value: Any) -> Any:
    meta = TUNABLE_SETTINGS[key]
    mn = meta.get("min")
    mx = meta.get("max")
    if mn is None or mx is None:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and meta["type"] == "int":
        return int(max(mn, min(mx, value)))
    return float(max(mn, min(mx, float(value))))


def _extract_stored(raw: dict[str, Any]) -> Any:
    if "value" in raw:
        return raw["value"]
    return raw


class HotConfig:
    """Load and persist tunable settings; used by `app.main` lifespan and settings router."""

    def __init__(self, db_url: str, redis_url: str) -> None:
        self._db_url = db_url
        self._redis_url = redis_url
        self._memory: dict[str, Any] = {}
        self._engine = create_async_engine(db_url, echo=False, pool_size=3, max_overflow=5)
        self._session_factory = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
        self._redis: redis_async.Redis | None
        try:
            self._redis = redis_async.from_url(redis_url, decode_responses=True)
        except Exception as exc:
            logger.warning("redis_client_init_failed", error=str(exc))
            self._redis = None

    def get(self, key: str) -> Any:
        if key not in TUNABLE_SETTINGS:
            raise KeyError(key)
        if key in self._memory:
            return self._memory[key]
        return TUNABLE_SETTINGS[key]["default"]

    def get_all(self) -> dict[str, Any]:
        return dict(self._memory)

    def get_by_category(self, category: str) -> dict[str, Any]:
        keys = [k for k, m in TUNABLE_SETTINGS.items() if m.get("category") == category]
        return {k: self.get(k) for k in keys}

    async def seed_defaults(self) -> None:
        async with self._session_factory() as session:
            for key, meta in TUNABLE_SETTINGS.items():
                existing = await session.scalar(select(Setting.id).where(Setting.key == key))
                if existing is None:
                    session.add(
                        Setting(
                            key=key,
                            value={"value": meta["default"]},
                            updated_by="seed",
                        )
                    )
            await session.commit()
        logger.info("hot_config_seed_complete", keys=len(TUNABLE_SETTINGS))

    async def load(self) -> None:
        self._memory = {k: meta["default"] for k, meta in TUNABLE_SETTINGS.items()}
        async with self._session_factory() as session:
            rows = (await session.execute(select(Setting))).scalars().all()
            for row in rows:
                if row.key not in TUNABLE_SETTINGS:
                    continue
                raw_val = _extract_stored(row.value)
                try:
                    coerced = _coerce_value(row.key, raw_val)
                    self._memory[row.key] = _clamp_value(row.key, coerced)
                except (TypeError, ValueError) as exc:
                    logger.warning("hot_config_coerce_failed", key=row.key, error=str(exc))
        await self._write_redis_snapshot()

    async def _write_redis_snapshot(self) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps(self._memory)
            await self._redis.set(REDIS_SNAPSHOT_KEY, payload)
        except Exception as exc:
            logger.warning("redis_snapshot_write_failed", error=str(exc))

    async def _invalidate_redis(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(REDIS_SNAPSHOT_KEY)
        except Exception as exc:
            logger.warning("redis_invalidate_failed", error=str(exc))

    async def set(self, key: str, value: Any, updated_by: str = "system") -> None:
        if key not in TUNABLE_SETTINGS:
            raise KeyError(key)
        coerced = _clamp_value(key, _coerce_value(key, value))
        async with self._session_factory() as session:
            row = await session.scalar(select(Setting).where(Setting.key == key))
            if row is None:
                session.add(Setting(key=key, value={"value": coerced}, updated_by=updated_by))
            else:
                row.value = {"value": coerced}
                row.updated_by = updated_by
            await session.commit()
        self._memory[key] = coerced
        await self._invalidate_redis()

    async def set_many(self, updates: dict[str, Any]) -> None:
        for k, v in updates.items():
            await self.set(k, v, updated_by="batch")

    async def reload(self) -> None:
        await self.load()

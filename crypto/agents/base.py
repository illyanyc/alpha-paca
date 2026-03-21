"""Base agent with health heartbeat, circuit breaker, and self-healing support."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog

from config import get_settings

if TYPE_CHECKING:
    from agents.healer import HealerAgent

logger = structlog.get_logger(__name__)

HEARTBEAT_PREFIX = "crypto:agent:heartbeat:"

_healer: HealerAgent | None = None
_state_ref: dict | None = None


def set_healer(healer: HealerAgent) -> None:
    global _healer
    _healer = healer


def set_state_ref(state: dict) -> None:
    global _state_ref
    _state_ref = state


def get_state_ref() -> dict | None:
    return _state_ref


BACKOFF_SCHEDULE = [2, 5, 15, 30, 60]


class BaseAgent(ABC):
    """Abstract base for all crypto agents with Redis heartbeat and self-healing."""

    name: str = "base"

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(settings.database.redis_url, decode_responses=True)
        return self._redis

    async def heartbeat(self) -> None:
        r = await self._get_redis()
        key = f"{HEARTBEAT_PREFIX}{self.name}"
        await r.set(key, datetime.now(timezone.utc).isoformat(), ex=60)

    @abstractmethod
    async def run(self, **kwargs) -> dict:
        ...

    async def safe_run(self, **kwargs) -> dict:
        """Run with circuit breaker, auto-healing, and error reporting."""
        healer = _healer

        # Circuit breaker check
        if healer:
            breaker = healer.get_breaker(self.name)
            if not breaker.allow_request():
                self._update_status("circuit_open")
                self._push_healing_status(
                    f"Circuit OPEN — paused until recovery timeout "
                    f"({int(breaker.recovery_timeout)}s)"
                )
                return {"error": "circuit_open", "agent": self.name}

        self._update_status("running")

        try:
            await self.heartbeat()
            result = await self.run(**kwargs)
            await self.heartbeat()

            if healer:
                healer.record_success(self.name)
            self._update_status("healthy")
            return result

        except Exception as e:
            logger.exception("agent_failed", agent=self.name, error=str(e))
            self._update_status("error")

            if not healer:
                return {"error": str(e), "agent": self.name}

            # AI-powered diagnosis and recovery
            event = await healer.diagnose_and_heal(
                agent_name=self.name,
                error=e,
                context={"kwargs_keys": list(kwargs.keys())},
            )

            self._push_healing_event(event)

            if event.outcome == "circuit_open":
                self._update_status("circuit_open")
                return {"error": str(e), "agent": self.name, "healing": "circuit_open"}

            if event.outcome == "skipped":
                self._update_status("healing")
                return {"error": str(e), "agent": self.name, "healing": "skipped"}

            # Retry logic
            if event.recovery_action in ("retry_immediate", "retry_with_backoff"):
                breaker = healer.get_breaker(self.name)
                backoff_idx = min(breaker.failure_count - 1, len(BACKOFF_SCHEDULE) - 1)
                wait = 0 if event.recovery_action == "retry_immediate" else BACKOFF_SCHEDULE[backoff_idx]

                self._update_status("healing")
                self._push_healing_status(
                    f"Retrying in {wait}s (attempt {breaker.failure_count})"
                )

                if wait > 0:
                    await asyncio.sleep(wait)

                try:
                    await self.heartbeat()
                    result = await self.run(**kwargs)
                    await self.heartbeat()
                    healer.record_success(self.name)
                    self._update_status("healthy")
                    self._push_healing_status("Self-healed successfully")
                    self._push_healing_event_update(event, "healed")
                    return result
                except Exception as retry_err:
                    logger.warning(
                        "retry_failed", agent=self.name, error=str(retry_err),
                    )
                    self._update_status("error")
                    return {"error": str(retry_err), "agent": self.name, "healing": "retry_failed"}

            return {"error": str(e), "agent": self.name, "healing": event.outcome}

    def _update_status(self, status: str) -> None:
        if _state_ref and "agent_statuses" in _state_ref:
            _state_ref["agent_statuses"][self.name] = status

    def think(self, step: str) -> None:
        """Emit an agent thinking/decision step to the live log."""
        if not _state_ref:
            return
        log = _state_ref.setdefault("agent_log", [])
        log.append({
            "agent": self.name,
            "step": step,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        _state_ref["agent_log"] = log[-50:]

    def _push_healing_status(self, message: str) -> None:
        if not _state_ref:
            return
        events = _state_ref.setdefault("healing_events", [])
        events.append({
            "agent": self.name,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _state_ref["healing_events"] = events[-30:]

    def _push_healing_event(self, event) -> None:
        if not _state_ref:
            return
        events = _state_ref.setdefault("healing_events", [])
        events.append({
            "agent": event.agent_name,
            "message": f"[{event.severity.upper()}] {event.error_message} → {event.recovery_action} ({event.outcome})",
            "diagnosis": event.diagnosis,
            "severity": event.severity,
            "action": event.recovery_action,
            "outcome": event.outcome,
            "confidence": event.confidence,
            "timestamp": event.timestamp,
        })
        _state_ref["healing_events"] = events[-30:]

    def _push_healing_event_update(self, event, new_outcome: str) -> None:
        if not _state_ref:
            return
        events = _state_ref.setdefault("healing_events", [])
        events.append({
            "agent": event.agent_name,
            "message": f"Self-healed: {event.error_message} → {new_outcome}",
            "severity": "info",
            "action": "healed",
            "outcome": new_outcome,
            "confidence": 1.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _state_ref["healing_events"] = events[-30:]

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

"""5-level circuit breaker hierarchy for trading system protection."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class BreakerLevel(IntEnum):
    CLOSED = 0
    TRANSIENT = 1
    DEGRADED = 2
    STRATEGY_HALT = 3
    SYSTEM_HALT = 4
    EMERGENCY = 5


LEVEL_NAMES = {
    BreakerLevel.CLOSED: "closed",
    BreakerLevel.TRANSIENT: "transient",
    BreakerLevel.DEGRADED: "degraded",
    BreakerLevel.STRATEGY_HALT: "strategy_halt",
    BreakerLevel.SYSTEM_HALT: "system_halt",
    BreakerLevel.EMERGENCY: "emergency",
}


class CircuitBreaker:
    """Hierarchical circuit breaker protecting the trading system.

    Levels:
        L0 CLOSED: Normal operation
        L1 TRANSIENT: Retry with exponential backoff (single API failure)
        L2 DEGRADED: Disable affected data source (3+ failures in 5 min)
        L3 STRATEGY_HALT: Halt specific pod (5 consecutive losses OR daily loss > 2%)
        L4 SYSTEM_HALT: Flatten all positions (max drawdown > 10% OR daily loss > 3%)
        L5 EMERGENCY: Emergency sell all (exchange unreachable > 5 min with open positions)
    """

    def __init__(self) -> None:
        self._system_level = BreakerLevel.CLOSED
        self._pod_levels: dict[str, BreakerLevel] = {}
        self._failure_counts: dict[str, list[float]] = defaultdict(list)
        self._consecutive_losses: dict[str, int] = defaultdict(int)
        self._daily_pnl: dict[str, float] = defaultdict(float)
        self._events: list[dict[str, Any]] = []
        self._last_exchange_contact: float = time.monotonic()

    @property
    def system_level(self) -> BreakerLevel:
        return self._system_level

    def get_pod_level(self, pod_name: str) -> BreakerLevel:
        return self._pod_levels.get(pod_name, BreakerLevel.CLOSED)

    def can_trade(self, pod_name: str) -> bool:
        if self._system_level >= BreakerLevel.SYSTEM_HALT:
            return False
        pod_level = self.get_pod_level(pod_name)
        return pod_level < BreakerLevel.STRATEGY_HALT

    def record_api_failure(self, source: str) -> BreakerLevel:
        now = time.monotonic()
        self._failure_counts[source].append(now)
        self._failure_counts[source] = [
            t for t in self._failure_counts[source] if now - t < 300
        ]

        count = len(self._failure_counts[source])
        if count >= 3:
            self._escalate_system(BreakerLevel.DEGRADED, f"3+ failures from {source} in 5 min")
            return BreakerLevel.DEGRADED
        elif count >= 1:
            return BreakerLevel.TRANSIENT
        return BreakerLevel.CLOSED

    def record_api_success(self, source: str) -> None:
        self._failure_counts[source].clear()
        self._last_exchange_contact = time.monotonic()

    def record_trade_result(self, pod_name: str, pnl: float, pnl_pct: float) -> None:
        if pnl <= 0:
            self._consecutive_losses[pod_name] += 1
        else:
            self._consecutive_losses[pod_name] = 0

        self._daily_pnl[pod_name] += pnl_pct

        if self._consecutive_losses[pod_name] >= 5:
            self._escalate_pod(
                pod_name,
                BreakerLevel.STRATEGY_HALT,
                f"5 consecutive losses in {pod_name}",
            )

        if self._daily_pnl[pod_name] <= -2.0:
            self._escalate_pod(
                pod_name,
                BreakerLevel.STRATEGY_HALT,
                f"daily loss > 2% in {pod_name}",
            )

    def check_drawdown(self, drawdown_pct: float, daily_loss_pct: float) -> None:
        if drawdown_pct >= 10.0 or daily_loss_pct >= 3.0:
            self._escalate_system(
                BreakerLevel.SYSTEM_HALT,
                f"drawdown={drawdown_pct:.1f}% daily_loss={daily_loss_pct:.1f}%",
            )

    def check_exchange_connectivity(self, has_open_positions: bool) -> None:
        elapsed = time.monotonic() - self._last_exchange_contact
        if elapsed > 300 and has_open_positions:
            self._escalate_system(
                BreakerLevel.EMERGENCY,
                f"exchange unreachable for {elapsed:.0f}s with open positions",
            )

    def reset_pod(self, pod_name: str) -> None:
        prev = self._pod_levels.get(pod_name, BreakerLevel.CLOSED)
        self._pod_levels[pod_name] = BreakerLevel.CLOSED
        self._consecutive_losses[pod_name] = 0
        self._daily_pnl[pod_name] = 0.0
        if prev != BreakerLevel.CLOSED:
            self._log_event(pod_name, "reset", prev, BreakerLevel.CLOSED)

    def reset_system(self) -> None:
        prev = self._system_level
        self._system_level = BreakerLevel.CLOSED
        self._daily_pnl.clear()
        self._consecutive_losses.clear()
        self._failure_counts.clear()
        if prev != BreakerLevel.CLOSED:
            self._log_event("system", "reset", prev, BreakerLevel.CLOSED)

    def reset_daily(self) -> None:
        self._daily_pnl.clear()
        for pod_name in list(self._pod_levels.keys()):
            if self._pod_levels[pod_name] == BreakerLevel.STRATEGY_HALT:
                self._pod_levels[pod_name] = BreakerLevel.CLOSED

    def get_status(self) -> dict[str, Any]:
        return {
            "system_level": LEVEL_NAMES[self._system_level],
            "pod_levels": {
                k: LEVEL_NAMES[v] for k, v in self._pod_levels.items()
            },
            "consecutive_losses": dict(self._consecutive_losses),
            "daily_pnl_pct": {k: round(v, 2) for k, v in self._daily_pnl.items()},
        }

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._events[-limit:]

    def _escalate_pod(self, pod_name: str, level: BreakerLevel, reason: str) -> None:
        prev = self._pod_levels.get(pod_name, BreakerLevel.CLOSED)
        if level > prev:
            self._pod_levels[pod_name] = level
            self._log_event(pod_name, reason, prev, level)
            logger.warning(
                "circuit_breaker_pod_escalation",
                pod=pod_name,
                prev=LEVEL_NAMES[prev],
                new=LEVEL_NAMES[level],
                reason=reason,
            )

    def _escalate_system(self, level: BreakerLevel, reason: str) -> None:
        prev = self._system_level
        if level > prev:
            self._system_level = level
            self._log_event("system", reason, prev, level)
            logger.critical(
                "circuit_breaker_system_escalation",
                prev=LEVEL_NAMES[prev],
                new=LEVEL_NAMES[level],
                reason=reason,
            )

    def _log_event(
        self, target: str, reason: str, prev: BreakerLevel, new: BreakerLevel
    ) -> None:
        self._events.append({
            "target": target,
            "reason": reason,
            "prev_level": LEVEL_NAMES[prev],
            "new_level": LEVEL_NAMES[new],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

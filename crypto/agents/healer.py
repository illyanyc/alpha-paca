"""Self-healing agent — uses AI to diagnose agent failures, decide recovery strategy,
and automatically restart agents with circuit-breaker protection."""

from __future__ import annotations

import asyncio
import traceback
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from config import get_settings

logger = structlog.get_logger(__name__)

MAX_HEALING_HISTORY = 50


class RecoveryAction(str, Enum):
    RETRY_IMMEDIATE = "retry_immediate"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    SKIP_CYCLE = "skip_cycle"
    RESET_STATE = "reset_state"
    CIRCUIT_BREAK = "circuit_break"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DiagnosisResult(BaseModel):
    root_cause: str
    severity: str  # critical / warning / transient
    recovery_action: str  # retry_immediate / retry_with_backoff / skip_cycle / reset_state / circuit_break
    explanation: str
    estimated_fix_confidence: float  # 0.0 - 1.0


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 1

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0
    half_open_calls: int = 0

    def record_success(self) -> None:
        self.failure_count = 0
        self.half_open_calls = 0
        if self.state != CircuitState.CLOSED:
            logger.info("circuit_closed", prev_state=self.state.value)
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("circuit_reopened", failures=self.failure_count)
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("circuit_opened", failures=self.failure_count)

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("circuit_half_open", elapsed_sec=round(elapsed))
                return True
            return False
        # HALF_OPEN
        if self.half_open_calls < self.half_open_max_calls:
            self.half_open_calls += 1
            return True
        return False

    def force_close(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0


@dataclass
class HealingEvent:
    agent_name: str
    error_type: str
    error_message: str
    diagnosis: str
    recovery_action: str
    severity: str
    outcome: str  # "healed" / "retrying" / "circuit_open" / "skipped"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 0.0


class HealerAgent:
    """AI-powered diagnostician that analyzes agent failures and prescribes recovery."""

    def __init__(self) -> None:
        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=(
                "You are a production systems reliability engineer for a crypto trading platform. "
                "An agent in the trading swarm has failed. Analyze the error and decide recovery.\n\n"
                "RULES:\n"
                "- 'transient' errors (network timeout, rate limit, API blip): retry_immediate or retry_with_backoff\n"
                "- 'warning' errors (bad data, parsing failure, stale cache): skip_cycle or reset_state\n"
                "- 'critical' errors (auth failure, DB down, config broken): circuit_break\n"
                "- Be concise. One sentence root_cause. One sentence explanation.\n"
                "- estimated_fix_confidence: how likely the suggested action resolves it (0.0-1.0)\n"
                "- If the same error repeats 3+ times, escalate severity.\n"
            ),
            output_type=DiagnosisResult,
        )
        self._breakers: dict[str, CircuitBreaker] = {}
        self._history: list[HealingEvent] = []
        self._error_counts: dict[str, int] = {}
        self._consecutive_fails: dict[str, int] = {}

    def get_breaker(self, agent_name: str) -> CircuitBreaker:
        if agent_name not in self._breakers:
            self._breakers[agent_name] = CircuitBreaker()
        return self._breakers[agent_name]

    @property
    def history(self) -> list[HealingEvent]:
        return self._history

    def _increment_error(self, agent_name: str, error_key: str) -> int:
        full_key = f"{agent_name}:{error_key}"
        self._error_counts[full_key] = self._error_counts.get(full_key, 0) + 1
        self._consecutive_fails[agent_name] = self._consecutive_fails.get(agent_name, 0) + 1
        return self._error_counts[full_key]

    def record_success(self, agent_name: str) -> None:
        self._consecutive_fails[agent_name] = 0
        breaker = self.get_breaker(agent_name)
        breaker.record_success()

    async def diagnose_and_heal(
        self,
        agent_name: str,
        error: Exception,
        context: dict[str, Any] | None = None,
    ) -> HealingEvent:
        """Diagnose an agent failure and return recovery recommendation."""
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        tb_str = "".join(tb[-6:])  # last 6 frames
        error_type = type(error).__name__
        error_msg = str(error)[:500]

        repeat_count = self._increment_error(agent_name, error_type)
        consec = self._consecutive_fails.get(agent_name, 1)
        breaker = self.get_breaker(agent_name)

        # Fast-path for known transient errors (skip AI call to save cost/latency)
        fast_diagnosis = self._fast_classify(error_type, error_msg, repeat_count, consec)
        if fast_diagnosis:
            event = self._apply_recovery(agent_name, fast_diagnosis, breaker)
            return event

        # AI diagnosis for unknown / complex errors
        try:
            prompt = (
                f"Agent: {agent_name}\n"
                f"Error type: {error_type}\n"
                f"Error message: {error_msg}\n"
                f"Consecutive failures: {consec}\n"
                f"Total occurrences of this error type: {repeat_count}\n"
                f"Circuit breaker state: {breaker.state.value}\n"
                f"Traceback (last 6 frames):\n```\n{tb_str}\n```\n"
            )
            if context:
                prompt += f"\nContext: {str(context)[:300]}\n"

            result = await self._agent.run(prompt)
            diagnosis = result.output
        except Exception as ai_err:
            logger.warning("healer_ai_fallback", error=str(ai_err))
            diagnosis = DiagnosisResult(
                root_cause=f"AI diagnosis unavailable: {error_type}",
                severity="warning" if consec < 3 else "critical",
                recovery_action="retry_with_backoff" if consec < 3 else "circuit_break",
                explanation="Falling back to rule-based recovery.",
                estimated_fix_confidence=0.3,
            )

        event = self._apply_recovery(agent_name, diagnosis, breaker)
        return event

    def _fast_classify(
        self, error_type: str, error_msg: str, repeat_count: int, consec: int,
    ) -> DiagnosisResult | None:
        msg_lower = error_msg.lower()

        if consec >= 5:
            return DiagnosisResult(
                root_cause=f"Repeated {error_type} ({consec}x consecutive)",
                severity="critical",
                recovery_action="circuit_break",
                explanation="Too many consecutive failures, opening circuit breaker.",
                estimated_fix_confidence=0.1,
            )

        if any(kw in msg_lower for kw in ["timeout", "timed out", "connection reset", "connection refused"]):
            action = "retry_immediate" if consec <= 1 else "retry_with_backoff"
            return DiagnosisResult(
                root_cause=f"Network/connection issue: {error_type}",
                severity="transient",
                recovery_action=action,
                explanation="Transient network error, retrying.",
                estimated_fix_confidence=0.8 if consec <= 1 else 0.5,
            )

        if any(kw in msg_lower for kw in ["rate limit", "429", "too many requests"]):
            return DiagnosisResult(
                root_cause="API rate limit hit",
                severity="transient",
                recovery_action="retry_with_backoff",
                explanation="Rate limited, backing off before retry.",
                estimated_fix_confidence=0.9,
            )

        if any(kw in msg_lower for kw in ["401", "403", "unauthorized", "forbidden", "auth"]):
            return DiagnosisResult(
                root_cause="Authentication/authorization failure",
                severity="critical",
                recovery_action="circuit_break",
                explanation="Auth error — cannot self-heal without new credentials.",
                estimated_fix_confidence=0.0,
            )

        if "json" in msg_lower or "parse" in msg_lower or "decode" in msg_lower:
            return DiagnosisResult(
                root_cause=f"Data parsing error: {error_type}",
                severity="warning",
                recovery_action="skip_cycle" if consec <= 2 else "reset_state",
                explanation="Bad data received, skipping this cycle.",
                estimated_fix_confidence=0.7,
            )

        return None

    def _apply_recovery(
        self, agent_name: str, diagnosis: DiagnosisResult, breaker: CircuitBreaker,
    ) -> HealingEvent:
        action = diagnosis.recovery_action
        outcome = "retrying"

        if action == "circuit_break":
            breaker.record_failure()
            breaker.state = CircuitState.OPEN
            breaker.last_failure_time = time.time()
            outcome = "circuit_open"
        elif action in ("retry_immediate", "retry_with_backoff"):
            breaker.record_failure()
            outcome = "retrying"
        elif action == "skip_cycle":
            outcome = "skipped"
        elif action == "reset_state":
            breaker.force_close()
            outcome = "healed"
        else:
            breaker.record_failure()
            outcome = "retrying"

        event = HealingEvent(
            agent_name=agent_name,
            error_type=type(Exception).__name__,
            error_message=diagnosis.root_cause,
            diagnosis=diagnosis.explanation,
            recovery_action=action,
            severity=diagnosis.severity,
            outcome=outcome,
            confidence=diagnosis.estimated_fix_confidence,
        )

        self._history.append(event)
        if len(self._history) > MAX_HEALING_HISTORY:
            self._history = self._history[-MAX_HEALING_HISTORY:]

        logger.info(
            "healing_event",
            agent=agent_name,
            severity=diagnosis.severity,
            action=action,
            outcome=outcome,
            root_cause=diagnosis.root_cause[:100],
            confidence=diagnosis.estimated_fix_confidence,
        )

        return event

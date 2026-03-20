"""System health validators — data feed freshness and agent heartbeats."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from app.models.validation import ValidationResult

logger = structlog.get_logger(__name__)

MAX_DATA_STALE_SECONDS = 120
MAX_HEARTBEAT_STALE_SECONDS = 60


class DataFreshnessValidator:
    """Checks that market-data feeds are recent enough to trade on."""

    @staticmethod
    def validate(last_tick_time: datetime | None) -> ValidationResult:
        if last_tick_time is None:
            return ValidationResult(
                validator_name="DataFreshnessValidator",
                verdict="fail",
                reason="no data timestamp available",
            )

        age = (datetime.now(timezone.utc) - last_tick_time).total_seconds()
        if age > MAX_DATA_STALE_SECONDS:
            return ValidationResult(
                validator_name="DataFreshnessValidator",
                verdict="fail",
                reason=f"data {age:.0f}s stale (max {MAX_DATA_STALE_SECONDS}s)",
            )
        return ValidationResult(
            validator_name="DataFreshnessValidator",
            verdict="pass",
            reason=f"data {age:.0f}s fresh",
        )


class AgentHealthValidator:
    """Checks that required agents have recent heartbeats."""

    @staticmethod
    def validate(
        heartbeats: dict[str, datetime],
        required_agents: list[str] | None = None,
    ) -> ValidationResult:
        if required_agents is None:
            required_agents = ["orchestrator", "risk_monitor"]

        now = datetime.now(timezone.utc)
        stale: list[str] = []

        for agent in required_agents:
            last_hb = heartbeats.get(agent)
            if last_hb is None:
                stale.append(f"{agent}:missing")
                continue
            age = (now - last_hb).total_seconds()
            if age > MAX_HEARTBEAT_STALE_SECONDS:
                stale.append(f"{agent}:{age:.0f}s")

        if stale:
            return ValidationResult(
                validator_name="AgentHealthValidator",
                verdict="fail",
                reason=f"stale agents: {', '.join(stale)}",
            )
        return ValidationResult(
            validator_name="AgentHealthValidator",
            verdict="pass",
            reason="all agents healthy",
        )


def run_system_checks(
    last_tick_time: datetime | None = None,
    heartbeats: dict[str, datetime] | None = None,
) -> list[ValidationResult]:
    """Run all system-health validators."""
    results: list[ValidationResult] = []
    results.append(DataFreshnessValidator.validate(last_tick_time))
    results.append(AgentHealthValidator.validate(heartbeats or {}))

    for r in results:
        if r.verdict == "fail":
            logger.error("system_check_fail", validator=r.validator_name, reason=r.reason)

    return results

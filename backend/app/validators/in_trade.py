"""In-trade validators — monitors open positions for exit conditions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.models.validation import ValidationResult, ValidatorContext

logger = structlog.get_logger(__name__)


class StopLossValidator:
    """Checks whether the position has hit its stop-loss."""

    @staticmethod
    def validate(
        position: dict[str, Any],
        context: ValidatorContext,
    ) -> ValidationResult:
        current_price = position.get("current_price", 0.0)
        stop_loss = position.get("stop_loss", 0.0)
        side = position.get("side", "long")

        hit = (
            (side == "long" and current_price <= stop_loss)
            or (side == "short" and current_price >= stop_loss)
        )
        if hit:
            return ValidationResult(
                validator_name="StopLossValidator",
                verdict="fail",
                reason=f"stop hit: price={current_price}, stop={stop_loss}",
            )
        return ValidationResult(
            validator_name="StopLossValidator",
            verdict="pass",
            reason="stop not hit",
        )


class TakeProfitValidator:
    """Checks whether any target price has been reached."""

    @staticmethod
    def validate(
        position: dict[str, Any],
        context: ValidatorContext,
    ) -> ValidationResult:
        current_price = position.get("current_price", 0.0)
        targets: dict[str, float] = position.get("target_prices") or {}
        side = position.get("side", "long")

        for label, target in targets.items():
            hit = (
                (side == "long" and current_price >= target)
                or (side == "short" and current_price <= target)
            )
            if hit:
                return ValidationResult(
                    validator_name="TakeProfitValidator",
                    verdict="warn",
                    reason=f"target {label} hit: price={current_price}, target={target}",
                )

        return ValidationResult(
            validator_name="TakeProfitValidator",
            verdict="pass",
            reason="no targets hit",
        )


class TimeDecayValidator:
    """Checks if a position has been held beyond its expected horizon."""

    MAX_HOLD_HOURS: int = 5 * 24  # 5 trading days

    @staticmethod
    def validate(
        position: dict[str, Any],
        context: ValidatorContext,
    ) -> ValidationResult:
        entry_time = position.get("entry_time")
        if entry_time is None:
            return ValidationResult(
                validator_name="TimeDecayValidator",
                verdict="pass",
                reason="no entry_time available",
            )

        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)

        now = datetime.now(timezone.utc)
        held_hours = (now - entry_time).total_seconds() / 3600
        max_hours = context.get("max_hold_hours", TimeDecayValidator.MAX_HOLD_HOURS)

        if held_hours > max_hours:
            return ValidationResult(
                validator_name="TimeDecayValidator",
                verdict="warn",
                reason=f"held {held_hours:.0f}h > max {max_hours}h",
            )
        return ValidationResult(
            validator_name="TimeDecayValidator",
            verdict="pass",
            reason=f"held {held_hours:.0f}h",
        )


_IN_TRADE_VALIDATORS = [
    StopLossValidator,
    TakeProfitValidator,
    TimeDecayValidator,
]


def run_in_trade_validators(
    position: dict[str, Any],
    context: ValidatorContext,
) -> list[ValidationResult]:
    """Execute all in-trade validators against an open position."""
    results: list[ValidationResult] = []
    for validator_cls in _IN_TRADE_VALIDATORS:
        result = validator_cls.validate(position, context)
        results.append(result)
        if result.verdict in ("fail", "warn"):
            logger.info(
                "in_trade_alert",
                validator=result.validator_name,
                symbol=position.get("symbol"),
                verdict=result.verdict,
                reason=result.reason,
            )
    return results

"""Pre-trade validators — liquidity, spread, and correlation checks."""

from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings
from app.models.signal import PodSignalOut
from app.models.validation import ValidationResult, ValidatorContext

logger = structlog.get_logger(__name__)


def _inject_hot_config(
    context: ValidatorContext,
    hot_config: dict[str, Any] | None,
) -> ValidatorContext:
    """Merge live hot-config overrides into the validator context, prefixed ``_hc_``."""
    if hot_config:
        for k, v in hot_config.items():
            context[f"_hc_{k}"] = v
    return context


class LiquidityValidator:
    """Checks minimum average volume and dollar volume."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        min_vol = context.get("_hc_min_avg_volume", settings.pre_trade.min_avg_volume)
        min_dv = context.get("_hc_min_avg_dollar_vol", settings.pre_trade.min_avg_dollar_vol)
        avg_volume = context.get("avg_volume", 0)
        avg_dollar_vol = context.get("avg_dollar_vol", 0)

        if avg_volume < min_vol:
            return ValidationResult(
                validator_name="LiquidityValidator",
                verdict="fail",
                reason=f"avg_volume {avg_volume} < {min_vol}",
            )
        if avg_dollar_vol < min_dv:
            return ValidationResult(
                validator_name="LiquidityValidator",
                verdict="fail",
                reason=f"avg_dollar_vol {avg_dollar_vol} < {min_dv}",
            )
        return ValidationResult(
            validator_name="LiquidityValidator",
            verdict="pass",
            reason="liquidity OK",
        )


class SpreadSlippageValidator:
    """Checks that the bid-ask spread is within acceptable bounds."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_spread = context.get("_hc_max_spread_pct", settings.pre_trade.max_spread_pct)
        spread_pct = context.get("spread_pct", 0.0)

        if spread_pct > max_spread:
            return ValidationResult(
                validator_name="SpreadSlippageValidator",
                verdict="fail",
                reason=f"spread {spread_pct:.2%} > max {max_spread:.2%}",
            )
        return ValidationResult(
            validator_name="SpreadSlippageValidator",
            verdict="pass",
            reason="spread OK",
        )


class CorrelationValidator:
    """Checks pairwise correlation with existing positions."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_corr = context.get(
            "_hc_max_pairwise_correlation",
            settings.pre_trade.max_pairwise_correlation,
        )
        correlations: list[float] = context.get("position_correlations", [])
        breaches = [c for c in correlations if abs(c) > max_corr]

        if breaches:
            return ValidationResult(
                validator_name="CorrelationValidator",
                verdict="fail",
                reason=f"{len(breaches)} positions exceed correlation limit {max_corr}",
            )
        return ValidationResult(
            validator_name="CorrelationValidator",
            verdict="pass",
            reason="correlation OK",
        )


_PRE_TRADE_VALIDATORS = [
    LiquidityValidator,
    SpreadSlippageValidator,
    CorrelationValidator,
]


def run_pre_trade_validators(
    signal: PodSignalOut,
    context: ValidatorContext,
    hot_config: dict[str, Any] | None = None,
) -> list[ValidationResult]:
    """Execute all pre-trade validators and return their results."""
    context = _inject_hot_config(context, hot_config)
    results: list[ValidationResult] = []
    for validator_cls in _PRE_TRADE_VALIDATORS:
        result = validator_cls.validate(signal, context)
        results.append(result)
        if result.verdict == "fail":
            logger.warning(
                "pre_trade_fail",
                validator=result.validator_name,
                symbol=signal.symbol,
                reason=result.reason,
            )
    return results

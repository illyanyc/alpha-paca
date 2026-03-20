"""Risk-gate validators — position sizing, exposure, VaR, and pod-overlap limits."""

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
    if hot_config:
        for k, v in hot_config.items():
            context[f"_hc_{k}"] = v
    return context


class PositionSizingValidator:
    """Ensures the proposed position stays within per-trade and portfolio limits."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_pos_pct = context.get("_hc_max_position_pct", settings.position_sizing.max_position_pct)
        position_pct = signal.position_size_pct

        if position_pct > max_pos_pct:
            return ValidationResult(
                validator_name="PositionSizingValidator",
                verdict="fail",
                reason=f"position {position_pct:.1f}% > max {max_pos_pct:.1f}%",
            )

        max_concurrent = context.get(
            "_hc_max_concurrent_positions",
            settings.position_sizing.max_concurrent_positions,
        )
        open_count = context.get("open_position_count", 0)
        if open_count >= max_concurrent:
            return ValidationResult(
                validator_name="PositionSizingValidator",
                verdict="fail",
                reason=f"open positions {open_count} >= max {max_concurrent}",
            )

        return ValidationResult(
            validator_name="PositionSizingValidator",
            verdict="pass",
            reason="position sizing OK",
        )


class PortfolioExposureValidator:
    """Checks gross and net exposure limits."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_gross = context.get("_hc_max_gross_exposure_pct", settings.risk.max_gross_exposure_pct)
        max_net = context.get("_hc_max_net_exposure_pct", settings.risk.max_net_exposure_pct)
        gross = context.get("gross_exposure_pct", 0.0)
        net = context.get("net_exposure_pct", 0.0)

        if gross > max_gross:
            return ValidationResult(
                validator_name="PortfolioExposureValidator",
                verdict="fail",
                reason=f"gross exposure {gross:.1f}% > max {max_gross:.1f}%",
            )
        if abs(net) > max_net:
            return ValidationResult(
                validator_name="PortfolioExposureValidator",
                verdict="fail",
                reason=f"net exposure {net:.1f}% > max {max_net:.1f}%",
            )
        return ValidationResult(
            validator_name="PortfolioExposureValidator",
            verdict="pass",
            reason="exposure OK",
        )


class FactorExposureValidator:
    """Checks aggregate factor exposures against limits."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_fe = context.get("_hc_max_factor_exposure", settings.risk.max_factor_exposure)
        exposures: dict[str, float] = context.get("factor_exposures", {})

        breaches = {f: v for f, v in exposures.items() if abs(v) > max_fe}
        if breaches:
            return ValidationResult(
                validator_name="FactorExposureValidator",
                verdict="fail",
                reason=f"factor breaches: {breaches}",
            )
        return ValidationResult(
            validator_name="FactorExposureValidator",
            verdict="pass",
            reason="factor exposure OK",
        )


class VaRLimitValidator:
    """Checks portfolio VaR against the configured daily limit."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_var = context.get("_hc_max_daily_var_pct", settings.risk.max_daily_var_pct)
        current_var = context.get("daily_var_pct", 0.0)

        if current_var > max_var:
            return ValidationResult(
                validator_name="VaRLimitValidator",
                verdict="fail",
                reason=f"VaR {current_var:.2f}% > max {max_var:.2f}%",
            )
        return ValidationResult(
            validator_name="VaRLimitValidator",
            verdict="pass",
            reason="VaR OK",
        )


class PodOverlapValidator:
    """Checks pod return correlation limits."""

    @staticmethod
    def validate(
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> ValidationResult:
        settings = get_settings()
        max_corr = context.get("_hc_max_pod_return_corr", settings.risk.max_pod_return_corr)
        pod_correlations: dict[str, float] = context.get("pod_correlations", {})

        breaches = {p: c for p, c in pod_correlations.items() if c > max_corr}
        if breaches:
            return ValidationResult(
                validator_name="PodOverlapValidator",
                verdict="warn",
                reason=f"pod correlation breaches: {breaches}",
            )
        return ValidationResult(
            validator_name="PodOverlapValidator",
            verdict="pass",
            reason="pod overlap OK",
        )


_RISK_GATE_VALIDATORS = [
    PositionSizingValidator,
    PortfolioExposureValidator,
    FactorExposureValidator,
    VaRLimitValidator,
    PodOverlapValidator,
]


def run_risk_gate_validators(
    signal: PodSignalOut,
    context: ValidatorContext,
    hot_config: dict[str, Any] | None = None,
) -> list[ValidationResult]:
    """Execute all risk-gate validators and return their results."""
    context = _inject_hot_config(context, hot_config)
    results: list[ValidationResult] = []
    for validator_cls in _RISK_GATE_VALIDATORS:
        result = validator_cls.validate(signal, context)
        results.append(result)
        if result.verdict == "fail":
            logger.warning(
                "risk_gate_fail",
                validator=result.validator_name,
                symbol=signal.symbol,
                reason=result.reason,
            )
    return results

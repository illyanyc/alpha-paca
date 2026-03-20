"""Main trading orchestrator — pre-market, intraday, and post-market pipelines."""

from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings
from app.engine import AlphaModel, FactorModel, SignalProcessor
from app.execution.order_manager import OrderManager
from app.execution.position_manager import PositionManager
from app.models.signal import PodSignalOut
from app.models.validation import ValidatorContext
from app.strategies import (
    EventDrivenPod,
    MeanReversionPod,
    MomentumPod,
    SectorRotationPod,
    StatArbPod,
)
from app.strategies.base_pod import BasePod
from app.validators.pre_trade import run_pre_trade_validators
from app.validators.risk_gate import run_risk_gate_validators

logger = structlog.get_logger(__name__)


class Orchestrator:
    """Coordinates the full trading loop across all strategy pods."""

    def __init__(self, hot_config: dict[str, Any] | None = None) -> None:
        self._settings = get_settings()
        self._hot_config = hot_config

        self._signal_processor = SignalProcessor()
        self._factor_model = FactorModel()
        self._alpha_model = AlphaModel(self._signal_processor, self._factor_model)
        self._order_manager = OrderManager()
        self._position_manager = PositionManager()

        self._pods: list[BasePod] = [
            MomentumPod(),
            MeanReversionPod(),
            EventDrivenPod(),
            SectorRotationPod(),
            StatArbPod(),
        ]

    def run_pre_market(self, universe: list[str] | None = None) -> dict[str, Any]:
        """Universe building and scanning across all pods."""
        if universe is None:
            universe = []

        scan_results: dict[str, list[dict[str, Any]]] = {}
        for pod in self._pods:
            name = pod.get_pod_name()
            candidates = pod.run_scan(universe)
            scan_results[name] = candidates
            logger.info("pod_scan_complete", pod=name, candidates=len(candidates))

        return {"universe_size": len(universe), "scan_results": scan_results}

    def run_intraday(
        self,
        scan_results: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Signal generation → validation → order submission."""
        submitted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for pod in self._pods:
            name = pod.get_pod_name()
            candidates = scan_results.get(name, [])
            if not candidates:
                continue

            signals = pod.generate_signals(candidates)
            for sig_dict in signals:
                signal = PodSignalOut(**sig_dict)
                context = self._build_context(signal)

                pre_results = self._run_pre_trade_checks(signal, context)
                if any(r.verdict == "fail" for r in pre_results):
                    rejected.append({"signal": sig_dict, "reason": "pre_trade"})
                    continue

                risk_results = self._run_risk_gate_checks(signal, context)
                if any(r.verdict == "fail" for r in risk_results):
                    rejected.append({"signal": sig_dict, "reason": "risk_gate"})
                    continue

                position_size = pod.compute_position_size(sig_dict, self._get_nav())
                order = self._order_manager.submit_order(signal, position_size)
                submitted.append(order)

        logger.info(
            "intraday_complete",
            submitted=len(submitted),
            rejected=len(rejected),
        )
        return {"submitted": submitted, "rejected": rejected}

    def run_post_market(self) -> dict[str, Any]:
        """Reconciliation, PnL calculation, and reporting."""
        positions = self._position_manager.sync_positions()
        positions = self._position_manager.update_pnl(positions)
        exits = self._position_manager.check_exits(positions)

        logger.info(
            "post_market_complete",
            open_positions=len(positions),
            exit_signals=len(exits),
        )
        return {
            "positions": len(positions),
            "exit_signals": len(exits),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_pre_trade_checks(
        self,
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> list:
        return run_pre_trade_validators(signal, context, self._hot_config)

    def _run_risk_gate_checks(
        self,
        signal: PodSignalOut,
        context: ValidatorContext,
    ) -> list:
        return run_risk_gate_validators(signal, context, self._hot_config)

    def _build_context(self, signal: PodSignalOut) -> ValidatorContext:
        """Assemble a validator context dict with current portfolio state."""
        return ValidatorContext(
            avg_volume=0,
            avg_dollar_vol=0,
            spread_pct=0.0,
            position_correlations=[],
            open_position_count=len(self._position_manager._positions),
            gross_exposure_pct=0.0,
            net_exposure_pct=0.0,
            factor_exposures={},
            daily_var_pct=0.0,
            pod_correlations={},
        )

    def _get_nav(self) -> float:
        max_tradable = self._settings.capital.max_tradable
        return max_tradable if max_tradable > 0 else 10_000.0

"""Main trading orchestrator — pre-market, intraday, and post-market pipelines."""

from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings
from app.engine import AlphaModel, FactorModel, SignalProcessor
from app.engine.circuit_breaker import CircuitBreaker
from app.engine.drift_detector import DriftDetector
from app.engine.kelly_sizer import KellySizer
from app.engine.regime import RegimeDetector, RegimeOutput, REGIME_POD_WEIGHTS
from app.execution.order_manager import OrderManager
from app.execution.position_manager import PositionManager
from app.models.signal import PodSignalOut
from app.models.validation import ValidatorContext
from app.services.alpaca_client import AlpacaService
from app.services.fmp_client import FMPClient
from app.services.market_data import MarketDataService
from app.services.news_pipeline import NewsPipeline
from app.strategies import (
    EventDrivenPod,
    MeanReversionPod,
    MomentumPod,
    SectorRotationPod,
    StatArbPod,
    VolatilityPod,
)
from app.strategies.base_pod import BasePod
from app.validators.pre_trade import run_pre_trade_validators
from app.validators.risk_gate import run_risk_gate_validators

logger = structlog.get_logger(__name__)


class Orchestrator:
    """Coordinates the full trading loop across all strategy pods."""

    def __init__(
        self,
        alpaca: AlpacaService,
        hot_config: Any | None = None,
        db_session: Any | None = None,
    ) -> None:
        self._settings = get_settings()
        self._hot_config = hot_config
        self._db_session = db_session
        self._alpaca = alpaca
        self._market_data = MarketDataService(alpaca=alpaca)
        self._last_scan_results: dict[str, list[dict[str, Any]]] = {}

        self._signal_processor = SignalProcessor()
        self._factor_model = FactorModel()
        self._alpha_model = AlphaModel(self._signal_processor, self._factor_model)
        self._order_manager = OrderManager(alpaca=alpaca)
        self._position_manager = PositionManager()

        self._regime_detector = RegimeDetector(alpaca)
        self._current_regime: RegimeOutput | None = None
        self._drift_detector = DriftDetector()
        self._circuit_breaker = CircuitBreaker()
        self._kelly_sizer = KellySizer(kelly_fraction=self._settings.kelly.fraction)

        self._pods: list[BasePod] = [
            MomentumPod(alpaca),
            MeanReversionPod(alpaca),
            EventDrivenPod(
                alpaca=alpaca,
                fmp=FMPClient(),
                news_pipeline=NewsPipeline(),
            ),
            SectorRotationPod(alpaca),
            StatArbPod(alpaca),
            VolatilityPod(alpaca),
        ]

    def run_pre_market(self, universe: list[str] | None = None) -> dict[str, Any]:
        """Universe building and scanning across all pods."""
        if universe is None:
            universe = []

        self._current_regime = self._regime_detector.detect(
            self._settings.regime.benchmark_symbol
        )
        logger.info(
            "regime_detected",
            dominant=self._current_regime.dominant.value,
            confidence=self._current_regime.confidence,
        )

        scan_results: dict[str, list[dict[str, Any]]] = {}
        for pod in self._pods:
            name = pod.get_pod_name()
            candidates = pod.run_scan(universe)
            scan_results[name] = candidates
            logger.info("pod_scan_complete", pod=name, candidates=len(candidates))

        self._last_scan_results = scan_results
        return {"universe_size": len(universe), "scan_results": scan_results}

    def run_intraday(
        self,
        scan_results: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Signal generation → validation → order submission."""
        if scan_results is None or not scan_results:
            scan_results = self._last_scan_results
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

                if not self._circuit_breaker.can_trade(pod.get_pod_name()):
                    rejected.append({"signal": sig_dict, "reason": "circuit_breaker"})
                    continue

                regime_state = self._current_regime.dominant if self._current_regime else None
                position_size = pod.compute_position_size(
                    sig_dict, self._get_nav(), self._kelly_sizer, regime_state
                )
                order = self._order_manager.submit_order(signal, position_size)
                submitted.append(order)

        fills = self._order_manager.monitor_fills()

        logger.info(
            "intraday_complete",
            submitted=len(submitted),
            rejected=len(rejected),
            fills=len(fills),
        )
        return {"submitted": submitted, "rejected": rejected}

    def run_post_market(self) -> dict[str, Any]:
        """Reconciliation, PnL calculation, and reporting."""
        positions = self._position_manager.sync_positions()
        positions = self._position_manager.update_pnl(positions)
        exits = self._position_manager.check_exits(positions)

        drift_events = self._drift_detector.check_all()
        if drift_events:
            logger.warning("drift_events_detected", count=len(drift_events))

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
        positions = []
        gross_exposure = 0.0
        net_exposure = 0.0
        try:
            positions = self._alpaca.get_positions()
            account = self._alpaca.get_account()
            equity = float(getattr(account, "equity", 0) or 0)
            if equity > 0:
                for pos in positions:
                    mv = abs(float(getattr(pos, "market_value", 0) or 0))
                    side_sign = 1.0 if getattr(pos, "side", "long") == "long" else -1.0
                    gross_exposure += mv
                    net_exposure += side_sign * mv
                gross_exposure = (gross_exposure / equity) * 100
                net_exposure = (net_exposure / equity) * 100
        except Exception:
            logger.warning("context_position_fetch_failed")

        avg_volume = 0
        avg_dollar_vol = 0
        spread_pct = 0.0
        try:
            bars = self._market_data.get_historical_bars(signal.symbol, "1Day", 20)
            if hasattr(bars, "data"):
                bar_list = bars.data.get(signal.symbol, [])
            else:
                bar_list = bars.get(signal.symbol, []) if isinstance(bars, dict) else []
            if bar_list:
                volumes = [float(b.volume) for b in bar_list]
                avg_volume = int(sum(volumes) / len(volumes))
                avg_prices = [(float(b.high) + float(b.low)) / 2 for b in bar_list]
                avg_dollar_vol = int(
                    sum(v * p for v, p in zip(volumes, avg_prices)) / len(volumes)
                )
                last_bar = bar_list[-1]
                if float(last_bar.close) > 0:
                    spread_pct = (
                        (float(last_bar.high) - float(last_bar.low))
                        / float(last_bar.close)
                        * 100
                    )
        except Exception:
            logger.warning("context_market_data_failed", symbol=signal.symbol)

        return ValidatorContext(
            avg_volume=avg_volume,
            avg_dollar_vol=avg_dollar_vol,
            spread_pct=spread_pct,
            position_correlations=[],
            open_position_count=len(positions),
            gross_exposure_pct=gross_exposure,
            net_exposure_pct=net_exposure,
            factor_exposures={},
            daily_var_pct=0.0,
            pod_correlations={},
        )

    def _get_nav(self) -> float:
        try:
            return self._alpaca.get_effective_nav(self._hot_config)
        except Exception:
            logger.warning("nav_fetch_failed_using_fallback")
            max_tradable = self._settings.capital.max_tradable
            return max_tradable if max_tradable > 0 else 10_000.0

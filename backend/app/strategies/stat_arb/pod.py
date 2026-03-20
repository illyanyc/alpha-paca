"""Statistical arbitrage strategy pod."""

from __future__ import annotations

from typing import Any

import structlog

from app.strategies.base_pod import BasePod
from app.strategies.stat_arb.scanner import StatArbScanner
from app.strategies.stat_arb.signals import StatArbSignalGenerator

logger = structlog.get_logger(__name__)


class StatArbPod(BasePod):
    """Pairs trading based on cointegration and spread z-scores."""

    def __init__(self) -> None:
        self._scanner = StatArbScanner()
        self._signal_gen = StatArbSignalGenerator()

    def get_pod_name(self) -> str:
        return "stat_arb"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

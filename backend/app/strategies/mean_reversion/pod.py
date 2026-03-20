"""Mean-reversion strategy pod."""

from __future__ import annotations

from typing import Any

import structlog

from app.strategies.base_pod import BasePod
from app.strategies.mean_reversion.scanner import MeanReversionScanner
from app.strategies.mean_reversion.signals import MeanReversionSignalGenerator

logger = structlog.get_logger(__name__)


class MeanReversionPod(BasePod):
    """Captures reversion to the mean via Bollinger bands and RSI extremes."""

    def __init__(self) -> None:
        self._scanner = MeanReversionScanner()
        self._signal_gen = MeanReversionSignalGenerator()

    def get_pod_name(self) -> str:
        return "mean_reversion"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

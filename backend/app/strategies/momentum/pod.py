"""Momentum strategy pod."""

from __future__ import annotations

from typing import Any

import structlog

from app.strategies.base_pod import BasePod
from app.strategies.momentum.scanner import MomentumScanner
from app.strategies.momentum.signals import MomentumSignalGenerator

logger = structlog.get_logger(__name__)


class MomentumPod(BasePod):
    """Captures directional moves via RSI, MACD, and price breakouts."""

    def __init__(self) -> None:
        self._scanner = MomentumScanner()
        self._signal_gen = MomentumSignalGenerator()

    def get_pod_name(self) -> str:
        return "momentum"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

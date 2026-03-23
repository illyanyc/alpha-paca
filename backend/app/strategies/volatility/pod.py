"""Volatility strategy pod — harvests crisis alpha via VIX mean reversion."""

from __future__ import annotations

from typing import Any

import structlog

from app.services.alpaca_client import AlpacaService
from app.strategies.base_pod import BasePod
from app.strategies.volatility.scanner import VolatilityScanner
from app.strategies.volatility.signals import VolatilitySignalGenerator

logger = structlog.get_logger(__name__)


class VolatilityPod(BasePod):
    """Captures crisis alpha via VIX mean reversion and volatility spread trading."""

    def __init__(self, alpaca: AlpacaService | None = None) -> None:
        self._scanner = VolatilityScanner(alpaca or AlpacaService())
        self._signal_gen = VolatilitySignalGenerator()

    def get_pod_name(self) -> str:
        return "volatility"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

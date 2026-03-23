"""Sector-rotation strategy pod."""

from __future__ import annotations

from typing import Any

import structlog

from app.services.alpaca_client import AlpacaService
from app.strategies.base_pod import BasePod
from app.strategies.sector_rotation.scanner import SectorRotationScanner
from app.strategies.sector_rotation.signals import SectorRotationSignalGenerator

logger = structlog.get_logger(__name__)


class SectorRotationPod(BasePod):
    """Rotates capital into sectors exhibiting relative strength."""

    def __init__(self, alpaca: AlpacaService | None = None) -> None:
        self._scanner = SectorRotationScanner(alpaca or AlpacaService())
        self._signal_gen = SectorRotationSignalGenerator()

    def get_pod_name(self) -> str:
        return "sector_rotation"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

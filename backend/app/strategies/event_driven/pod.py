"""Event-driven strategy pod."""

from __future__ import annotations

from typing import Any

import structlog

from app.strategies.base_pod import BasePod
from app.strategies.event_driven.scanner import EventDrivenScanner
from app.strategies.event_driven.signals import EventDrivenSignalGenerator

logger = structlog.get_logger(__name__)


class EventDrivenPod(BasePod):
    """Trades catalysts: earnings surprises, news events, and corporate actions."""

    def __init__(self) -> None:
        self._scanner = EventDrivenScanner()
        self._signal_gen = EventDrivenSignalGenerator()

    def get_pod_name(self) -> str:
        return "event_driven"

    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        return self._scanner.scan(universe)

    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw = self._signal_gen.generate(candidates)
        return [s for s in raw if self.validate_signal(s)]

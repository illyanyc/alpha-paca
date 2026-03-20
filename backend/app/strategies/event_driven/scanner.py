"""Event-driven scanner — monitors earnings calendar and news catalysts."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EventDrivenScanner:
    """Identifies symbols with upcoming or recent catalysts."""

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for symbol in universe:
            candidate = self._score_symbol(symbol)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: c["catalyst_score"], reverse=True)
        logger.info("event_driven_scan_complete", candidates=len(candidates))
        return candidates

    def _score_symbol(self, symbol: str) -> dict[str, Any] | None:
        return {
            "symbol": symbol,
            "catalyst_type": None,
            "earnings_date": None,
            "surprise_pct": 0.0,
            "news_sentiment": 0.0,
            "catalyst_score": 0.0,
            "last_price": 0.0,
        }

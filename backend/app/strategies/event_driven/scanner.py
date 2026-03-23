"""Event-driven scanner — monitors earnings calendar and news catalysts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

VOLUME_SURGE_THRESHOLD = 1.5
GAP_THRESHOLD_PCT = 2.0


class EventDrivenScanner:
    """Identifies symbols with upcoming or recent catalysts.

    Accepts optional pre-fetched ``earnings_data`` and ``news_data`` from
    the orchestrator (async FMP / NewsPipeline results). When unavailable,
    falls back to price-action heuristics (gap + volume surge).
    """

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca

    def scan(
        self,
        universe: list[str],
        earnings_data: list[dict[str, Any]] | None = None,
        news_data: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        earnings_by_sym: dict[str, dict[str, Any]] = {}
        if earnings_data:
            for e in earnings_data:
                sym = (e.get("symbol") or "").upper()
                if sym:
                    earnings_by_sym[sym] = e

        news_by_sym: dict[str, list[dict[str, Any]]] = {}
        if news_data:
            for n in news_data:
                sym = (n.get("symbol") or "").upper()
                news_by_sym.setdefault(sym, []).append(n)

        candidates: list[dict[str, Any]] = []
        for symbol in universe:
            candidate = self._score_symbol(
                symbol,
                earnings=earnings_by_sym.get(symbol.upper()),
                news=news_by_sym.get(symbol.upper()),
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: c["catalyst_score"], reverse=True)
        logger.info("event_driven_scan_complete", candidates=len(candidates))
        return candidates

    def _score_symbol(
        self,
        symbol: str,
        earnings: dict[str, Any] | None = None,
        news: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        try:
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars_list = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if not bars_list or len(bars_list) < 5:
                return None
        except Exception:
            logger.warning("event_scanner_data_fetch_failed", symbol=symbol)
            return None

        closes = np.array([float(b.close) for b in bars_list])
        volumes = np.array([float(b.volume) for b in bars_list])
        opens = np.array([float(b.open) for b in bars_list])
        last_price = float(closes[-1])

        gap_pct = abs(opens[-1] - closes[-2]) / (closes[-2] + 1e-9) * 100 if len(closes) >= 2 else 0.0
        avg_volume = float(np.mean(volumes[:-1])) if len(volumes) > 1 else 1.0
        volume_ratio = float(volumes[-1]) / (avg_volume + 1e-9)

        price_action_score = 0.0
        if gap_pct > GAP_THRESHOLD_PCT:
            price_action_score += min(gap_pct / 10.0, 0.4)
        if volume_ratio > VOLUME_SURGE_THRESHOLD:
            price_action_score += min((volume_ratio - 1.0) / 4.0, 0.3)

        earnings_score = 0.0
        earnings_date = None
        surprise_pct = 0.0
        catalyst_type = None
        if earnings:
            earnings_date = earnings.get("date") or earnings.get("earningsDate")
            surprise_pct = float(earnings.get("surprise_pct", 0.0) or 0.0)
            earnings_score = min(abs(surprise_pct) / 20.0, 0.5)
            catalyst_type = "earnings"

        news_sentiment = 0.0
        if news:
            news_sentiment = min(len(news) / 10.0, 0.3)
            if catalyst_type is None:
                catalyst_type = "news"

        catalyst_score = float(
            0.4 * price_action_score + 0.35 * earnings_score + 0.25 * news_sentiment
        )

        return {
            "symbol": symbol,
            "catalyst_type": catalyst_type,
            "earnings_date": earnings_date,
            "surprise_pct": surprise_pct,
            "news_sentiment": news_sentiment,
            "catalyst_score": catalyst_score,
            "last_price": last_price,
        }

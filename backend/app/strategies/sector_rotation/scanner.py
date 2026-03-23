"""Sector-rotation scanner — analyses relative sector strength."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

ROTATION_LOOKBACK_DAYS = 20
BENCHMARK_ETF = "SPY"


class SectorRotationScanner:
    """Ranks sectors by relative performance and selects leaders/laggards."""

    SECTOR_ETFS: dict[str, str] = {
        "technology": "XLK",
        "healthcare": "XLV",
        "financials": "XLF",
        "energy": "XLE",
        "consumer_disc": "XLY",
        "consumer_staples": "XLP",
        "industrials": "XLI",
        "materials": "XLB",
        "utilities": "XLU",
        "real_estate": "XLRE",
        "communication": "XLC",
    }

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca

    def _fetch_closes(self, symbol: str, days: int) -> np.ndarray | None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        try:
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars_list = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if not bars_list:
                return None
            return np.array([float(b.close) for b in bars_list])
        except Exception:
            logger.warning("sector_scanner_data_fetch_failed", symbol=symbol)
            return None

    def scan(self, universe: list[str] | None = None) -> list[dict[str, Any]]:
        benchmark_closes = self._fetch_closes(BENCHMARK_ETF, ROTATION_LOOKBACK_DAYS + 5)
        if benchmark_closes is None or len(benchmark_closes) < 2:
            logger.warning("sector_scanner_benchmark_unavailable")
            benchmark_returns = np.zeros(0)
        else:
            benchmark_returns = np.diff(benchmark_closes) / benchmark_closes[:-1]

        candidates: list[dict[str, Any]] = []
        for sector, etf in self.SECTOR_ETFS.items():
            candidate = self._score_sector(sector, etf, benchmark_returns)
            candidates.append(candidate)
        candidates.sort(key=lambda c: c["relative_strength"], reverse=True)
        logger.info("sector_rotation_scan_complete", candidates=len(candidates))
        return candidates

    def _score_sector(
        self,
        sector: str,
        etf: str,
        benchmark_returns: np.ndarray,
    ) -> dict[str, Any]:
        closes = self._fetch_closes(etf, ROTATION_LOOKBACK_DAYS + 5)
        if closes is None or len(closes) < 2:
            return {
                "symbol": etf,
                "sector": sector,
                "relative_strength": 0.0,
                "momentum_20d": 0.0,
                "volume_trend": 0.0,
                "rotation_score": 0.0,
            }

        sector_returns = np.diff(closes) / closes[:-1]
        momentum_20d = float((closes[-1] / closes[0] - 1.0) * 100)

        rel_strength = self.compute_relative_strength(sector_returns, benchmark_returns)
        rotation_score = float(0.6 * rel_strength + 0.4 * (momentum_20d / 100.0))

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=ROTATION_LOOKBACK_DAYS + 5)
        volume_trend = 0.0
        try:
            barset = self._alpaca.get_bars(etf, "1Day", start, end)
            bars_list = barset.data.get(etf, []) if hasattr(barset, "data") else barset.get(etf, [])
            if bars_list and len(bars_list) > 5:
                volumes = np.array([float(b.volume) for b in bars_list])
                recent_avg = float(np.mean(volumes[-5:]))
                older_avg = float(np.mean(volumes[:-5])) or 1.0
                volume_trend = (recent_avg / older_avg) - 1.0
        except Exception:
            pass

        return {
            "symbol": etf,
            "sector": sector,
            "relative_strength": rel_strength,
            "momentum_20d": momentum_20d,
            "volume_trend": volume_trend,
            "rotation_score": rotation_score,
        }

    @staticmethod
    def compute_relative_strength(
        sector_returns: np.ndarray,
        benchmark_returns: np.ndarray,
    ) -> float:
        """Cumulative excess return over benchmark in the lookback window."""
        if len(sector_returns) == 0:
            return 0.0
        excess = sector_returns - benchmark_returns[: len(sector_returns)]
        return float(np.sum(excess))

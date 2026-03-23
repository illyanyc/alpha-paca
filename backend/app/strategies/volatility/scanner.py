"""Volatility scanner — monitors VIX and vol-related ETFs for trading opportunities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

VOL_INSTRUMENTS = {
    "VIXY": "VIX Short-Term Futures",
    "SVXY": "Short VIX Short-Term Futures",
    "UVXY": "1.5x Long VIX Short-Term Futures",
    "VXX": "VIX Short-Term Futures ETN",
}

VIX_PROXY = "VIXY"
VIX_MEAN = 20.0
VIX_STD_LOOKBACK = 60


class VolatilityScanner:
    """Scans VIX proxy instruments for mean-reversion and spike opportunities."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        for symbol, description in VOL_INSTRUMENTS.items():
            candidate = self._score_vol_instrument(symbol, description)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: abs(c.get("vol_z_score", 0)), reverse=True)
        logger.info("volatility_scan_complete", candidates=len(candidates))
        return candidates

    def _score_vol_instrument(self, symbol: str, description: str) -> dict[str, Any] | None:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=VIX_STD_LOOKBACK + 10)
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if len(bars) < 20:
                return None

            closes = np.array([float(b.close) for b in bars])
            volumes = np.array([float(b.volume) for b in bars])

            current_price = closes[-1]
            rolling_mean = float(np.mean(closes[-VIX_STD_LOOKBACK:]))
            rolling_std = float(np.std(closes[-VIX_STD_LOOKBACK:]))

            if rolling_std < 1e-9:
                return None

            vol_z_score = (current_price - rolling_mean) / rolling_std

            vol_of_vol = float(np.std(np.diff(np.log(closes[-20:]))) * np.sqrt(252))

            avg_volume = float(np.mean(volumes[-20:]))

            returns_5d = (closes[-1] / closes[-5] - 1) * 100 if len(closes) >= 5 else 0.0

            return {
                "symbol": symbol,
                "description": description,
                "last_price": current_price,
                "vol_z_score": vol_z_score,
                "rolling_mean": rolling_mean,
                "rolling_std": rolling_std,
                "vol_of_vol": vol_of_vol,
                "avg_volume": avg_volume,
                "returns_5d": returns_5d,
                "vol_score": abs(vol_z_score),
            }
        except Exception:
            logger.warning("vol_scanner_failed", symbol=symbol)
            return None

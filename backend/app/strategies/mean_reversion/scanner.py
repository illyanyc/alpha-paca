"""Mean-reversion scanner — finds oversold / overbought conditions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService
from app.strategies.momentum.scanner import MomentumScanner

logger = structlog.get_logger(__name__)

BB_PERIOD = 20
BB_STD_MULT = 2.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


class MeanReversionScanner:
    """Scans for symbols trading at Bollinger-band extremes or RSI tails."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for symbol in universe:
            candidate = self._score_symbol(symbol)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: abs(c["bb_z"]), reverse=True)
        logger.info("mean_reversion_scan_complete", candidates=len(candidates))
        return candidates

    def _score_symbol(self, symbol: str) -> dict[str, Any] | None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=40)
        try:
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars_list = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if not bars_list:
                return None
            closes = np.array([float(b.close) for b in bars_list])
        except Exception:
            logger.warning("mean_reversion_scanner_data_fetch_failed", symbol=symbol)
            return None

        if len(closes) < BB_PERIOD:
            return None

        upper, mid, lower = self.compute_bollinger(closes)
        last_price = float(closes[-1])
        bb_z = self.bb_z_score(last_price, upper, lower)
        rsi = MomentumScanner.compute_rsi(closes)

        rsi_contrib = 0.0
        if rsi < RSI_OVERSOLD:
            rsi_contrib = (RSI_OVERSOLD - rsi) / RSI_OVERSOLD
        elif rsi > RSI_OVERBOUGHT:
            rsi_contrib = (rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT)

        reversion_score = float(0.6 * abs(bb_z) + 0.4 * rsi_contrib)

        return {
            "symbol": symbol,
            "bb_z": bb_z,
            "rsi": rsi,
            "last_price": last_price,
            "mean_price": mid,
            "reversion_score": reversion_score,
        }

    @staticmethod
    def compute_bollinger(
        closes: np.ndarray,
        period: int = BB_PERIOD,
        std_mult: float = BB_STD_MULT,
    ) -> tuple[float, float, float]:
        """Return (upper, middle, lower) Bollinger bands."""
        if len(closes) < period:
            mid = float(closes[-1]) if len(closes) else 0.0
            return mid, mid, mid
        window = closes[-period:]
        mid = float(np.mean(window))
        std = float(np.std(window))
        return mid + std_mult * std, mid, mid - std_mult * std

    @staticmethod
    def bb_z_score(price: float, upper: float, lower: float) -> float:
        """Standardised distance within the Bollinger channel (−1 … +1 nominal)."""
        band_width = upper - lower
        if band_width == 0:
            return 0.0
        mid = (upper + lower) / 2
        return (price - mid) / (band_width / 2)

"""Momentum universe scanner — identifies stocks with strong directional moves."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BREAKOUT_LOOKBACK = 20

RSI_WEIGHT = 0.35
MACD_WEIGHT = 0.35
BREAKOUT_WEIGHT = 0.30


class MomentumScanner:
    """Scans a universe for momentum candidates using RSI, MACD, and price breakouts."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for symbol in universe:
            candidate = self._score_symbol(symbol)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: c["momentum_score"], reverse=True)
        logger.info("momentum_scan_complete", candidates=len(candidates))
        return candidates

    def _score_symbol(self, symbol: str) -> dict[str, Any] | None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=60)
        try:
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars_list = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if not bars_list:
                return None
            closes = np.array([float(b.close) for b in bars_list])
        except Exception:
            logger.warning("momentum_scanner_data_fetch_failed", symbol=symbol)
            return None

        if len(closes) < MACD_SLOW + MACD_SIGNAL:
            return None

        rsi = self.compute_rsi(closes)
        _, _, macd_hist = self.compute_macd(closes)
        breakout = self.is_breakout(closes)

        rsi_norm = (rsi - 50.0) / 50.0
        macd_norm = np.clip(macd_hist / (float(np.std(closes)) + 1e-9), -1.0, 1.0)
        breakout_val = 1.0 if breakout else 0.0

        momentum_score = float(
            RSI_WEIGHT * rsi_norm
            + MACD_WEIGHT * macd_norm
            + BREAKOUT_WEIGHT * breakout_val
        )

        return {
            "symbol": symbol,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "breakout_flag": breakout,
            "momentum_score": momentum_score,
        }

    @staticmethod
    def compute_rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> float:
        """Relative Strength Index."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = float(np.mean(gains[-period:]))
        avg_loss = float(np.mean(losses[-period:])) or 1e-9
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))

    @staticmethod
    def compute_macd(
        closes: np.ndarray,
    ) -> tuple[float, float, float]:
        """MACD line, signal line, and histogram value."""
        if len(closes) < MACD_SLOW + MACD_SIGNAL:
            return 0.0, 0.0, 0.0

        def _ema(data: np.ndarray, span: int) -> np.ndarray:
            alpha = 2 / (span + 1)
            out = np.empty_like(data)
            out[0] = data[0]
            for i in range(1, len(data)):
                out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
            return out

        fast = _ema(closes, MACD_FAST)
        slow = _ema(closes, MACD_SLOW)
        macd_line = fast - slow
        signal_line = _ema(macd_line, MACD_SIGNAL)
        histogram = macd_line - signal_line
        return float(macd_line[-1]), float(signal_line[-1]), float(histogram[-1])

    @staticmethod
    def is_breakout(closes: np.ndarray, lookback: int = BREAKOUT_LOOKBACK) -> bool:
        """True if the latest close exceeds the lookback-period high."""
        if len(closes) < lookback + 1:
            return False
        return float(closes[-1]) > float(np.max(closes[-lookback - 1 : -1]))

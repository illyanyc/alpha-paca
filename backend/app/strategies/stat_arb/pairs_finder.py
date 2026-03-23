"""Cointegration testing for pairs trading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

COINT_P_VALUE_THRESHOLD = 0.05
MIN_HALF_LIFE = 1
MAX_HALF_LIFE = 60
LOOKBACK_DAYS = 120

try:
    from statsmodels.tsa.stattools import adfuller as _adfuller

    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False


class PairsFinder:
    """Finds cointegrated pairs from a universe of symbols."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca
        self._price_cache: dict[str, np.ndarray] = {}

    def _fetch_closes(self, symbol: str) -> np.ndarray | None:
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=LOOKBACK_DAYS)
        try:
            barset = self._alpaca.get_bars(symbol, "1Day", start, end)
            bars_list = barset.data.get(symbol, []) if hasattr(barset, "data") else barset.get(symbol, [])
            if not bars_list:
                return None
            closes = np.array([float(b.close) for b in bars_list])
            self._price_cache[symbol] = closes
            return closes
        except Exception:
            logger.warning("pairs_finder_data_fetch_failed", symbol=symbol)
            return None

    def find_pairs(
        self,
        universe: list[str],
    ) -> list[dict[str, Any]]:
        self._price_cache.clear()
        pairs: list[dict[str, Any]] = []
        for i in range(len(universe)):
            for j in range(i + 1, len(universe)):
                sym_a, sym_b = universe[i], universe[j]
                result = self._test_pair(sym_a, sym_b)
                if result is not None and result.get("method") == "adf_failed":
                    johansen_result = self._test_pair_johansen(sym_a, sym_b)
                    if johansen_result is not None:
                        result = johansen_result
                    else:
                        result = None
                if result is not None:
                    pairs.append(result)
        logger.info("pairs_finder_complete", tested=len(universe), found=len(pairs))
        return pairs

    def _test_pair(
        self,
        symbol_a: str,
        symbol_b: str,
    ) -> dict[str, Any] | None:
        if not _HAS_STATSMODELS:
            logger.warning("statsmodels_not_installed")
            return None

        prices_a = self._fetch_closes(symbol_a)
        prices_b = self._fetch_closes(symbol_b)
        if prices_a is None or prices_b is None:
            return None

        min_len = min(len(prices_a), len(prices_b))
        if min_len < 30:
            return None
        prices_a = prices_a[-min_len:]
        prices_b = prices_b[-min_len:]

        hedge_ratio = self.compute_hedge_ratio(prices_a, prices_b)
        spread = prices_a - hedge_ratio * prices_b

        try:
            adf_result = _adfuller(spread, maxlag=1, regression="c", autolag=None)
            p_value = float(adf_result[1])
        except Exception:
            logger.warning("adf_test_failed", pair=f"{symbol_a}/{symbol_b}")
            return None

        if p_value > COINT_P_VALUE_THRESHOLD:
            return {
                "symbol_a": symbol_a,
                "symbol_b": symbol_b,
                "p_value": p_value,
                "half_life": float("inf"),
                "hedge_ratio": hedge_ratio,
                "method": "adf_failed",
            }

        half_life = self.compute_half_life(spread)
        if half_life < MIN_HALF_LIFE or half_life > MAX_HALF_LIFE:
            return None

        return {
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "p_value": p_value,
            "half_life": half_life,
            "hedge_ratio": hedge_ratio,
            "method": "adf",
        }

    def _test_pair_johansen(self, symbol_a: str, symbol_b: str) -> dict[str, Any] | None:
        """Test cointegration using the Johansen procedure."""
        closes_a = self._fetch_closes(symbol_a)
        closes_b = self._fetch_closes(symbol_b)
        if closes_a is None or closes_b is None:
            return None
        if len(closes_a) != len(closes_b):
            min_len = min(len(closes_a), len(closes_b))
            closes_a, closes_b = closes_a[-min_len:], closes_b[-min_len:]
        try:
            from statsmodels.tsa.vector_ar.vecm import coint_johansen

            data = np.column_stack([closes_a, closes_b])
            result = coint_johansen(data, det_order=0, k_ar_diff=1)
            trace_stat = result.lr1[0]
            crit_90 = result.cvt[0, 0]
            is_cointegrated = trace_stat > crit_90
            if not is_cointegrated:
                return None
            hedge_ratio = float(result.evec[1, 0] / result.evec[0, 0]) if result.evec[0, 0] != 0 else 1.0
            spread = closes_a - hedge_ratio * closes_b
            half_life = self.compute_half_life(spread)
            return {
                "symbol_a": symbol_a,
                "symbol_b": symbol_b,
                "p_value": 0.01,
                "half_life": half_life,
                "hedge_ratio": hedge_ratio,
                "method": "johansen",
                "trace_stat": float(trace_stat),
            }
        except ImportError:
            return None
        except Exception:
            logger.warning("johansen_test_failed", a=symbol_a, b=symbol_b)
            return None

    @staticmethod
    def compute_half_life(spread: np.ndarray) -> float:
        """Mean-reversion half-life via OLS on lagged spread."""
        if len(spread) < 3:
            return float("inf")
        lag = spread[:-1]
        delta = np.diff(spread)
        lag_mean = lag - np.mean(lag)
        denom = np.dot(lag_mean, lag_mean)
        if denom == 0:
            return float("inf")
        beta = np.dot(lag_mean, delta) / denom
        if beta >= 0:
            return float("inf")
        return float(-np.log(2) / beta)

    @staticmethod
    def compute_hedge_ratio(
        prices_a: np.ndarray,
        prices_b: np.ndarray,
    ) -> float:
        """OLS hedge ratio: regress A on B."""
        if len(prices_a) < 2:
            return 1.0
        b_mean = prices_b - np.mean(prices_b)
        denom = np.dot(b_mean, b_mean)
        if denom == 0:
            return 1.0
        return float(np.dot(b_mean, prices_a - np.mean(prices_a)) / denom)

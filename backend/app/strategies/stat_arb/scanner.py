"""Stat-arb scanner — finds cointegrated pairs."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from app.services.alpaca_client import AlpacaService
from app.strategies.stat_arb.pairs_finder import PairsFinder

logger = structlog.get_logger(__name__)


class StatArbScanner:
    """Scans for pairs with significant cointegration using the Engle-Granger test."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca
        self._finder = PairsFinder(alpaca)

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        pairs = self._finder.find_pairs(universe)
        candidates: list[dict[str, Any]] = []
        for pair in pairs:
            prices_a = self._finder._price_cache.get(pair["symbol_a"])
            prices_b = self._finder._price_cache.get(pair["symbol_b"])

            spread_z = 0.0
            last_price_a = 0.0
            last_price_b = 0.0
            if prices_a is not None and prices_b is not None:
                min_len = min(len(prices_a), len(prices_b))
                pa = prices_a[-min_len:]
                pb = prices_b[-min_len:]
                hedge = pair.get("hedge_ratio", 1.0)
                spread = pa - hedge * pb
                spread_mean = float(np.mean(spread))
                spread_std = float(np.std(spread))
                if spread_std > 0:
                    spread_z = float((spread[-1] - spread_mean) / spread_std)
                last_price_a = float(pa[-1])
                last_price_b = float(pb[-1])

            candidates.append(
                {
                    "symbol_a": pair["symbol_a"],
                    "symbol_b": pair["symbol_b"],
                    "symbol": f"{pair['symbol_a']}/{pair['symbol_b']}",
                    "p_value": pair["p_value"],
                    "half_life": pair["half_life"],
                    "spread_z": spread_z,
                    "last_price_a": last_price_a,
                    "last_price_b": last_price_b,
                    "hedge_ratio": pair.get("hedge_ratio", 1.0),
                }
            )
        candidates.sort(key=lambda c: c["p_value"])
        logger.info("stat_arb_scan_complete", pairs=len(candidates))
        return candidates

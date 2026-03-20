"""Stat-arb scanner — finds cointegrated pairs."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from app.strategies.stat_arb.pairs_finder import PairsFinder

logger = structlog.get_logger(__name__)


class StatArbScanner:
    """Scans for pairs with significant cointegration using the Engle-Granger test."""

    def __init__(self) -> None:
        self._finder = PairsFinder()

    def scan(self, universe: list[str]) -> list[dict[str, Any]]:
        pairs = self._finder.find_pairs(universe)
        candidates: list[dict[str, Any]] = []
        for pair in pairs:
            candidates.append(
                {
                    "symbol_a": pair["symbol_a"],
                    "symbol_b": pair["symbol_b"],
                    "symbol": f"{pair['symbol_a']}/{pair['symbol_b']}",
                    "p_value": pair["p_value"],
                    "half_life": pair["half_life"],
                    "spread_z": 0.0,
                    "last_price_a": 0.0,
                    "last_price_b": 0.0,
                    "hedge_ratio": pair.get("hedge_ratio", 1.0),
                }
            )
        candidates.sort(key=lambda c: c["p_value"])
        logger.info("stat_arb_scan_complete", pairs=len(candidates))
        return candidates

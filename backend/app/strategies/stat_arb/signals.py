"""Stat-arb signal generator — trades on spread z-score extremes."""

from __future__ import annotations

from typing import Any

import structlog

from app.engine.regime.models import RegimeOutput

logger = structlog.get_logger(__name__)

Z_ENTRY_THRESHOLD = 2.0
Z_EXIT_THRESHOLD = 0.5
DEFAULT_STOP_Z = 3.5
DEFAULT_TARGET_MULTIPLES = [1.0, 1.5]


class StatArbSignalGenerator:
    """Generates entry/exit signals when pair spreads reach z-score extremes."""

    def generate(
        self,
        candidates: list[dict[str, Any]],
        regime: RegimeOutput | None = None,
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        for c in candidates:
            signal = self._build_signal(c, regime=regime)
            if signal is not None:
                signals.append(signal)
        return signals

    def _build_signal(
        self,
        candidate: dict[str, Any],
        regime: RegimeOutput | None = None,
    ) -> dict[str, Any] | None:
        spread_z = candidate.get("spread_z", 0.0)
        if abs(spread_z) < Z_ENTRY_THRESHOLD:
            return None

        side = "short" if spread_z > Z_ENTRY_THRESHOLD else "long"
        score = min(abs(spread_z) / 4.0, 1.0)

        entry_price = candidate.get("last_price_a", 0.0)
        hedge_ratio = candidate.get("hedge_ratio", 1.0)

        return {
            "symbol": candidate.get("symbol", ""),
            "symbol_a": candidate.get("symbol_a", ""),
            "symbol_b": candidate.get("symbol_b", ""),
            "pod_name": "stat_arb",
            "signal_name": "pairs_spread",
            "alpha_score": score,
            "z_score": spread_z,
            "ic_weight": min(max((1.0 - candidate.get("p_value", 1.0)) * 0.15, 0.03), 0.30),
            "composite_score": score,
            "side": side,
            "entry_price": entry_price,
            "hedge_ratio": hedge_ratio,
            "stop_loss": 0.0,
            "targets": {},
            "trade_type": "pairs",
            "urgency": "normal",
        }

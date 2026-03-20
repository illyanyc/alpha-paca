"""Sector-rotation signal generator."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

ROTATION_SCORE_THRESHOLD = 0.10
DEFAULT_STOP_PCT = 0.04
DEFAULT_TARGET_MULTIPLES = [1.5, 2.5]


class SectorRotationSignalGenerator:
    """Generates long signals for leading sectors, shorts for laggards."""

    def generate(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []

        signals: list[dict[str, Any]] = []

        top = [c for c in candidates if c.get("relative_strength", 0) > ROTATION_SCORE_THRESHOLD]
        bottom = [c for c in candidates if c.get("relative_strength", 0) < -ROTATION_SCORE_THRESHOLD]

        for c in top:
            sig = self._build_signal(c, side="long")
            if sig:
                signals.append(sig)

        for c in bottom:
            sig = self._build_signal(c, side="short")
            if sig:
                signals.append(sig)

        return signals

    def _build_signal(
        self,
        candidate: dict[str, Any],
        side: str,
    ) -> dict[str, Any] | None:
        entry_price = candidate.get("last_price", 0.0) or 0.0
        rs = abs(candidate.get("relative_strength", 0.0))
        score = min(rs / 0.30, 1.0)

        if score < 0.1:
            return None

        stop_loss = (
            entry_price * (1 - DEFAULT_STOP_PCT)
            if side == "long"
            else entry_price * (1 + DEFAULT_STOP_PCT)
        )
        risk = abs(entry_price - stop_loss) or 1e-9
        targets = {
            f"t{i + 1}": entry_price + (risk * m if side == "long" else -risk * m)
            for i, m in enumerate(DEFAULT_TARGET_MULTIPLES)
        }

        return {
            "symbol": candidate["symbol"],
            "pod_name": "sector_rotation",
            "signal_name": "sector_momentum",
            "alpha_score": score,
            "z_score": 0.0,
            "ic_weight": 0.0,
            "composite_score": score,
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "targets": targets,
            "trade_type": "swing",
            "urgency": "normal",
        }

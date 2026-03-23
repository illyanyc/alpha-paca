"""Mean-reversion signal generator."""

from __future__ import annotations

from typing import Any

import structlog

from app.engine.regime.models import RegimeOutput, RegimeState

logger = structlog.get_logger(__name__)

BB_Z_ENTRY = 1.5
DEFAULT_STOP_PCT = 0.04
DEFAULT_TARGET_MULTIPLES = [1.0, 2.0]


class MeanReversionSignalGenerator:
    """Generates entry/exit signals based on mean-reversion setups."""

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
        bb_z = candidate.get("bb_z", 0.0)
        rsi = candidate.get("rsi", 50.0)
        entry_price = candidate.get("last_price", 0.0)
        mean_price = candidate.get("mean_price", entry_price)

        z_entry = BB_Z_ENTRY
        if regime is not None:
            if regime.dominant == RegimeState.SIDEWAYS:
                z_entry = 1.2
            elif regime.dominant == RegimeState.BULL_TREND:
                z_entry = 2.0

        if abs(bb_z) < z_entry:
            return None

        side = "long" if bb_z < -z_entry else "short"
        score = min(abs(bb_z) / 3.0, 1.0)

        if (side == "long" and rsi < 30) or (side == "short" and rsi > 70):
            score += 0.2

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
            "pod_name": "mean_reversion",
            "signal_name": "bb_reversion",
            "alpha_score": score,
            "z_score": bb_z,
            "ic_weight": min(max(abs(bb_z) * 0.08, 0.03), 0.30),
            "composite_score": score,
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "targets": targets,
            "trade_type": "swing",
            "urgency": "normal",
        }

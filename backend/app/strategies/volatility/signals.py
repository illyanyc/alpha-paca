"""Volatility signal generator — VIX mean reversion and vol spike signals."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

VOL_Z_ENTRY_SHORT = 2.0
VOL_Z_ENTRY_LONG = -1.5
DEFAULT_STOP_PCT = 0.08
DEFAULT_TARGET_MULTIPLES = [1.0, 2.0]


class VolatilitySignalGenerator:
    """Generates signals for volatility instruments based on z-score extremes."""

    def generate(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        for c in candidates:
            signal = self._build_signal(c)
            if signal is not None:
                signals.append(signal)
        return signals

    def _build_signal(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        vol_z = candidate.get("vol_z_score", 0.0)
        entry_price = candidate.get("last_price", 0.0)
        vol_of_vol = candidate.get("vol_of_vol", 0.0)

        if entry_price <= 0:
            return None

        score = 0.0
        side = "long"
        signal_name = "vol_mean_reversion"

        if vol_z >= VOL_Z_ENTRY_SHORT:
            side = "short"
            score = min(vol_z / 4.0, 1.0)
            signal_name = "vol_spike_short"
        elif vol_z <= VOL_Z_ENTRY_LONG:
            side = "long"
            score = min(abs(vol_z) / 3.0, 1.0)
            signal_name = "vol_dip_long"
        else:
            return None

        if vol_of_vol > 0.5:
            score *= 0.8

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

        ic_weight = min(max(score * 0.12, 0.03), 0.30)

        return {
            "symbol": candidate["symbol"],
            "pod_name": "volatility",
            "signal_name": signal_name,
            "alpha_score": score,
            "z_score": vol_z,
            "ic_weight": ic_weight,
            "composite_score": score,
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "targets": targets,
            "trade_type": "swing",
            "urgency": "high" if abs(vol_z) > 3.0 else "normal",
        }

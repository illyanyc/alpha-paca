"""Momentum signal generator — entry/exit signals from momentum indicators."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
DEFAULT_STOP_PCT = 0.03
DEFAULT_TARGET_MULTIPLES = [1.5, 2.5, 3.5]


class MomentumSignalGenerator:
    """Converts scanner candidates into scored alpha signals."""

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
        """Score a candidate and produce a signal dict if it passes thresholds."""
        rsi = candidate.get("rsi", 50.0)
        macd_hist = candidate.get("macd_hist", 0.0)
        breakout = candidate.get("breakout_flag", False)
        entry_price = candidate.get("last_price", 0.0)

        score = 0.0
        side = "long"

        if rsi > RSI_OVERBOUGHT and macd_hist > 0:
            score += 0.4
        elif rsi < RSI_OVERSOLD and macd_hist < 0:
            score += 0.3
            side = "short"

        if breakout:
            score += 0.3

        score += candidate.get("momentum_score", 0.0) * 0.3

        if score < 0.1:
            return None

        stop_loss = entry_price * (1 - DEFAULT_STOP_PCT) if side == "long" else entry_price * (1 + DEFAULT_STOP_PCT)
        risk = abs(entry_price - stop_loss) or 1e-9
        targets = {
            f"t{i + 1}": entry_price + (risk * m if side == "long" else -risk * m)
            for i, m in enumerate(DEFAULT_TARGET_MULTIPLES)
        }

        return {
            "symbol": candidate["symbol"],
            "pod_name": "momentum",
            "signal_name": "momentum_breakout",
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

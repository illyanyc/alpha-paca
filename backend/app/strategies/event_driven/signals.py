"""Event-driven signal generator."""

from __future__ import annotations

from typing import Any

import structlog

from app.engine.regime.models import RegimeOutput

logger = structlog.get_logger(__name__)

SURPRISE_THRESHOLD = 5.0
DEFAULT_STOP_PCT = 0.05
DEFAULT_TARGET_MULTIPLES = [1.5, 3.0]


class EventDrivenSignalGenerator:
    """Generates signals from earnings surprises, news sentiment, and corporate actions."""

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
        catalyst_score = candidate.get("catalyst_score", 0.0)
        surprise_pct = candidate.get("surprise_pct", 0.0)
        sentiment = candidate.get("news_sentiment", 0.0)
        entry_price = candidate.get("last_price", 0.0)

        score = catalyst_score
        if abs(surprise_pct) >= SURPRISE_THRESHOLD:
            score += 0.3

        if sentiment > 0.5:
            score += 0.2
        elif sentiment < -0.5:
            score += 0.15

        if score < 0.1:
            return None

        side = "long" if surprise_pct >= 0 and sentiment >= 0 else "short"
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
            "pod_name": "event_driven",
            "signal_name": "catalyst_event",
            "alpha_score": score,
            "z_score": 0.0,
            "ic_weight": min(max(score * 0.12, 0.03), 0.30),
            "composite_score": score,
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "targets": targets,
            "trade_type": "event",
            "urgency": "high" if abs(surprise_pct) >= SURPRISE_THRESHOLD else "normal",
        }

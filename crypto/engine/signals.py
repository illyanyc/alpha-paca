"""Dynamic signal combiner — accuracy-weighted, regime-modulated composite scoring.

Replaces fixed 45/30/25 weights with adaptive weights based on recent
accuracy, regime context, and conflict detection.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalStrength(str, Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


SIGNAL_SCORES = {
    SignalStrength.STRONG_BUY: 1.0,
    SignalStrength.BUY: 0.5,
    SignalStrength.NEUTRAL: 0.0,
    SignalStrength.SELL: -0.5,
    SignalStrength.STRONG_SELL: -1.0,
}


@dataclass
class ComponentSignal:
    source: str
    signal: SignalStrength
    score: float
    confidence: float
    details: str = ""


def classify_technical(indicators: dict[str, float | None]) -> ComponentSignal:
    """Convert raw indicator values into a technical signal."""
    score = 0.0
    reasons: list[str] = []

    rsi_val = indicators.get("rsi")
    if rsi_val is not None:
        if rsi_val < 30:
            score += 0.25
            reasons.append(f"RSI oversold ({rsi_val:.0f})")
        elif rsi_val < 45:
            score += 0.10
        elif rsi_val > 70:
            score -= 0.25
            reasons.append(f"RSI overbought ({rsi_val:.0f})")
        elif rsi_val > 55:
            score -= 0.05

    macd_hist = indicators.get("macd_hist")
    if macd_hist is not None:
        if macd_hist > 0:
            score += 0.20
            reasons.append("MACD bullish")
        else:
            score -= 0.20
            reasons.append("MACD bearish")

    close = indicators.get("close")
    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    bb_middle = indicators.get("bb_middle")
    if close is not None and bb_upper is not None and bb_lower is not None:
        if close < bb_lower:
            score += 0.20
            reasons.append("Below lower BB")
        elif close > bb_upper:
            score -= 0.15
            reasons.append("Above upper BB")
        elif bb_middle is not None and close > bb_middle:
            score += 0.05

    vwap_val = indicators.get("vwap")
    if close is not None and vwap_val is not None:
        if close > vwap_val:
            score += 0.10
            reasons.append("Above VWAP")
        else:
            score -= 0.10
            reasons.append("Below VWAP")

    wr = indicators.get("williams_r")
    if wr is not None:
        if wr < -80:
            score += 0.15
            reasons.append(f"Williams %R oversold ({wr:.0f})")
        elif wr > -20:
            score -= 0.15

    ema_9 = indicators.get("ema_9")
    ema_21 = indicators.get("ema_21")
    if ema_9 is not None and ema_21 is not None:
        if ema_9 > ema_21:
            score += 0.15
            reasons.append("EMA 9>21 bullish")
        else:
            score -= 0.10
            reasons.append("EMA 9<21 bearish")

    mom_5 = indicators.get("momentum_5")
    mom_10 = indicators.get("momentum_10")
    if mom_5 is not None:
        if mom_5 > 0.02:
            score += 0.10
        elif mom_5 < -0.02:
            score -= 0.10
    if mom_10 is not None:
        if mom_10 > 0.03:
            score += 0.10
        elif mom_10 < -0.03:
            score -= 0.10

    vol = indicators.get("volume")
    vol_sma = indicators.get("volume_sma")
    if vol is not None and vol_sma is not None and vol_sma > 0:
        vol_ratio = vol / vol_sma
        if vol_ratio > 2.0:
            score *= 1.4
            reasons.append(f"Volume spike ({vol_ratio:.1f}x)")
        elif vol_ratio > 1.2:
            score *= 1.1

    score = max(-1.0, min(1.0, score))
    confidence = min(1.0, abs(score) * 1.5 + 0.15)

    if score >= 0.5:
        sig = SignalStrength.STRONG_BUY
    elif score >= 0.15:
        sig = SignalStrength.BUY
    elif score <= -0.5:
        sig = SignalStrength.STRONG_SELL
    elif score <= -0.15:
        sig = SignalStrength.SELL
    else:
        sig = SignalStrength.NEUTRAL

    return ComponentSignal(
        source="technical",
        signal=sig,
        score=score,
        confidence=confidence,
        details="; ".join(reasons),
    )


# ── Dynamic Combiner ────────────────────────────────────────────────


DEFAULT_WEIGHTS = {
    "technical": 0.35,
    "news": 0.15,
    "fundamental": 0.10,
    "strategy": 0.25,
    "microstructure": 0.10,
    "onchain": 0.05,
}

REGIME_WEIGHT_MODIFIERS = {
    "trending_up": {"strategy": 1.5, "technical": 1.3, "microstructure": 0.7},
    "trending_down": {"strategy": 1.5, "technical": 1.3, "microstructure": 0.7},
    "mean_reverting": {"strategy": 1.5, "technical": 1.0, "microstructure": 1.2},
    "volatile": {"microstructure": 1.5, "strategy": 1.3, "onchain": 1.5},
}


@dataclass
class AccuracyTracker:
    """Tracks per-source signal accuracy over a rolling window."""
    history: dict[str, deque] = field(default_factory=dict)
    max_entries: int = 100

    def record(self, source: str, predicted_direction: int, actual_direction: int) -> None:
        if source not in self.history:
            self.history[source] = deque(maxlen=self.max_entries)
        correct = 1 if predicted_direction == actual_direction else 0
        self.history[source].append(correct)

    def accuracy(self, source: str) -> float:
        if source not in self.history or len(self.history[source]) < 5:
            return 0.5
        h = self.history[source]
        return sum(h) / len(h)

    def to_dict(self) -> dict[str, float]:
        return {src: self.accuracy(src) for src in self.history}


_accuracy_tracker = AccuracyTracker()


def get_accuracy_tracker() -> AccuracyTracker:
    return _accuracy_tracker


def composite_score(signals: list[ComponentSignal]) -> tuple[float, float]:
    """Legacy fixed-weight composite for backward compatibility."""
    weights = {"technical": 0.45, "news": 0.30, "fundamental": 0.25}
    total_weight = 0.0
    weighted_score = 0.0
    weighted_conf = 0.0

    for s in signals:
        w = weights.get(s.source, 0.2)
        weighted_score += s.score * w * s.confidence
        weighted_conf += s.confidence * w
        total_weight += w

    if total_weight == 0:
        return 0.0, 0.0
    return weighted_score / total_weight, weighted_conf / total_weight


def dynamic_composite(
    signals: dict[str, dict[str, Any]],
    regime: str | None = None,
    strategy_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Dynamic accuracy-weighted signal combination with regime modulation.

    signals: {source: {"score": float, "confidence": float, ...}}
    Returns {"score": float, "confidence": float, "action": str, "details": dict}
    """
    base_weights = dict(DEFAULT_WEIGHTS)
    regime_mods = REGIME_WEIGHT_MODIFIERS.get(regime, {}) if regime else {}

    for source, mod in regime_mods.items():
        if source in base_weights:
            base_weights[source] *= mod

    tracker = get_accuracy_tracker()
    for source in base_weights:
        acc = tracker.accuracy(source)
        base_weights[source] *= (0.5 + acc)

    total = sum(base_weights.values())
    if total > 0:
        for k in base_weights:
            base_weights[k] /= total

    if strategy_signals:
        strat_score = 0.0
        strat_conf = 0.0
        strat_count = 0
        for s in strategy_signals:
            if abs(s.get("score", 0)) > 0.1:
                strat_score += s["score"]
                strat_conf += s.get("confidence", 0)
                strat_count += 1
        if strat_count > 0:
            signals["strategy"] = {
                "score": strat_score / strat_count,
                "confidence": strat_conf / strat_count,
            }

    weighted_score = 0.0
    weighted_conf = 0.0
    total_weight = 0.0
    source_contrib: dict[str, float] = {}

    for source, data in signals.items():
        w = base_weights.get(source, 0.05)
        s = data.get("score", 0)
        c = data.get("confidence", 0)
        contribution = s * w * c
        weighted_score += contribution
        weighted_conf += c * w
        total_weight += w
        source_contrib[source] = round(contribution, 4)

    if total_weight > 0:
        final_score = weighted_score / total_weight
        final_conf = weighted_conf / total_weight
    else:
        final_score = 0.0
        final_conf = 0.0

    high_conviction_signals = [
        (src, d) for src, d in signals.items()
        if abs(d.get("score", 0)) > 0.5 and d.get("confidence", 0) > 0.6
    ]

    has_conflict = False
    if len(high_conviction_signals) >= 2:
        directions = set(1 if d["score"] > 0 else -1 for _, d in high_conviction_signals)
        if len(directions) > 1:
            has_conflict = True
            final_score *= 0.3
            final_conf *= 0.5

    final_score = max(-1.0, min(1.0, final_score))
    final_conf = max(0.0, min(1.0, final_conf))

    if final_score > 0.15 and final_conf > 0.4:
        action = "BUY"
    elif final_score < -0.15 and final_conf > 0.4:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "score": round(final_score, 4),
        "confidence": round(final_conf, 4),
        "action": action,
        "has_conflict": has_conflict,
        "weights": {k: round(v, 3) for k, v in base_weights.items()},
        "contributions": source_contrib,
        "regime": regime,
    }

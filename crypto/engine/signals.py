"""Dynamic signal combiner — Adaptive Momentum composite scoring.

Uses the 50/30/20 weight scheme (Technical / News-Sentiment / On-Chain)
with accuracy tracking and regime modulation.  Composite range: -100 to +100.
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
    """Convert raw indicator values into a technical signal.

    Uses Adaptive Momentum rules: RSI(5) > 50 is bullish (momentum, not
    mean-reversion), MACD(8-17-9) cross, EMA(8/21), VWAP bias, volume.
    """
    score = 0.0
    reasons: list[str] = []

    rsi_5 = indicators.get("rsi_5")
    if rsi_5 is not None:
        if rsi_5 > 70:
            score += 0.30
            reasons.append(f"RSI(5) strong momentum ({rsi_5:.0f})")
        elif rsi_5 > 50:
            score += 0.20
            reasons.append(f"RSI(5) bullish ({rsi_5:.0f})")
        elif rsi_5 < 40:
            score -= 0.25
            reasons.append(f"RSI(5) weak ({rsi_5:.0f})")
        elif rsi_5 < 50:
            score -= 0.05
    else:
        rsi_14 = indicators.get("rsi")
        if rsi_14 is not None:
            if rsi_14 > 50:
                score += 0.10
            elif rsi_14 < 50:
                score -= 0.10

    macd_4h_line = indicators.get("macd_4h_line")
    macd_4h_signal = indicators.get("macd_4h_signal")
    macd_4h_bullish_cross = indicators.get("macd_4h_bullish_cross", False)
    if macd_4h_line is not None and macd_4h_signal is not None:
        if macd_4h_line > macd_4h_signal and macd_4h_line > 0:
            score += 0.25
            reasons.append("MACD(8-17-9) bullish above zero")
        elif macd_4h_line > macd_4h_signal:
            score += 0.10
            reasons.append("MACD(8-17-9) bullish cross")
        elif macd_4h_line < macd_4h_signal:
            score -= 0.20
            reasons.append("MACD(8-17-9) bearish")
        if macd_4h_bullish_cross:
            score += 0.10
            reasons.append("MACD fresh bullish crossover")
    else:
        macd_hist = indicators.get("macd_hist")
        if macd_hist is not None:
            if macd_hist > 0:
                score += 0.15
            else:
                score -= 0.15

    ema_8 = indicators.get("ema_8")
    ema_21 = indicators.get("ema_21")
    if ema_8 is not None and ema_21 is not None:
        if ema_8 > ema_21:
            score += 0.15
            reasons.append("EMA(8) > EMA(21)")
        else:
            score -= 0.10
            reasons.append("EMA(8) < EMA(21)")

    close = indicators.get("close")
    vwap_val = indicators.get("vwap")
    if close is not None and vwap_val is not None and vwap_val > 0:
        if close > vwap_val:
            score += 0.10
            reasons.append("Above VWAP")
        else:
            score -= 0.10
            reasons.append("Below VWAP")

    vol_ratio = indicators.get("vol_ratio_20")
    if vol_ratio is not None:
        if vol_ratio > 2.0:
            score *= 1.4
            reasons.append(f"Volume spike ({vol_ratio:.1f}x)")
        elif vol_ratio > 1.2:
            score *= 1.15
            reasons.append(f"Volume confirms ({vol_ratio:.1f}x)")
    else:
        vol = indicators.get("volume")
        vol_sma = indicators.get("volume_sma")
        if vol is not None and vol_sma is not None and vol_sma > 0:
            ratio = vol / vol_sma
            if ratio > 2.0:
                score *= 1.4
            elif ratio > 1.2:
                score *= 1.15

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
    "technical": 0.50,
    "news": 0.30,
    "onchain": 0.20,
}

REGIME_WEIGHT_MODIFIERS = {
    "trending_up": {"technical": 1.3, "news": 1.0, "onchain": 0.8},
    "trending_down": {"technical": 1.3, "news": 1.0, "onchain": 0.8},
    "mean_reverting": {"technical": 0.8, "news": 1.2, "onchain": 1.3},
    "volatile": {"technical": 0.9, "news": 1.3, "onchain": 1.3},
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
    weights = {"technical": 0.50, "news": 0.30, "fundamental": 0.20}
    total_weight = 0.0
    weighted_score = 0.0
    weighted_conf = 0.0

    for s in signals:
        w = weights.get(s.source, 0.1)
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
    Returns {"score": float, "confidence": float, "action": str, ...}

    Score is in -100..+100 range.  BUY if > 40, SELL/EXIT if < -20.
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
            signals["technical"] = {
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

    composite_100 = max(-100.0, min(100.0, final_score * 100))
    final_conf = max(0.0, min(1.0, final_conf))

    if composite_100 > 40 and final_conf > 0.4:
        action = "BUY"
    elif composite_100 < -20 and final_conf > 0.3:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "score": round(final_score, 4),
        "composite_100": round(composite_100, 2),
        "confidence": round(final_conf, 4),
        "action": action,
        "has_conflict": has_conflict,
        "weights": {k: round(v, 3) for k, v in base_weights.items()},
        "contributions": source_contrib,
        "regime": regime,
    }

"""Signal normalization and composite scoring for crypto trading."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    source: str  # technical / news / fundamental
    signal: SignalStrength
    score: float  # -1.0 to 1.0
    confidence: float  # 0.0 to 1.0
    details: str = ""


def classify_technical(indicators: dict[str, float | None]) -> ComponentSignal:
    """Convert raw indicator values into a technical signal."""
    score = 0.0
    reasons: list[str] = []

    rsi_val = indicators.get("rsi")
    if rsi_val is not None:
        if rsi_val < 30:
            score += 0.3
            reasons.append(f"RSI oversold ({rsi_val:.1f})")
        elif rsi_val > 70:
            score -= 0.3
            reasons.append(f"RSI overbought ({rsi_val:.1f})")

    macd_hist = indicators.get("macd_hist")
    if macd_hist is not None:
        if macd_hist > 0:
            score += 0.25
            reasons.append("MACD bullish")
        else:
            score -= 0.25
            reasons.append("MACD bearish")

    close = indicators.get("close")
    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    if close is not None and bb_upper is not None and bb_lower is not None:
        if close < bb_lower:
            score += 0.2
            reasons.append("Below lower Bollinger band")
        elif close > bb_upper:
            score -= 0.2
            reasons.append("Above upper Bollinger band")

    vwap_val = indicators.get("vwap")
    if close is not None and vwap_val is not None:
        if close > vwap_val:
            score += 0.15
            reasons.append("Above VWAP")
        else:
            score -= 0.15
            reasons.append("Below VWAP")

    vol = indicators.get("volume")
    vol_sma = indicators.get("volume_sma")
    if vol is not None and vol_sma is not None and vol_sma > 0:
        vol_ratio = vol / vol_sma
        if vol_ratio > 2.0:
            score *= 1.3
            reasons.append(f"Volume spike ({vol_ratio:.1f}x)")

    score = max(-1.0, min(1.0, score))
    confidence = min(1.0, abs(score) * 1.2)

    if score >= 0.6:
        sig = SignalStrength.STRONG_BUY
    elif score >= 0.2:
        sig = SignalStrength.BUY
    elif score <= -0.6:
        sig = SignalStrength.STRONG_SELL
    elif score <= -0.2:
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


def composite_score(signals: list[ComponentSignal]) -> tuple[float, float]:
    """Weighted average of all signal sources.

    Returns (score, confidence) where score is -1 to 1 and confidence is 0 to 1.
    """
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

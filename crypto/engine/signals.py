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
    """Convert raw indicator values into a technical signal.

    Uses RSI, MACD, Bollinger Bands, VWAP, Williams %R, EMA crossover,
    momentum, and volume to produce a composite score from -1 to 1.
    """
    score = 0.0
    reasons: list[str] = []

    rsi_val = indicators.get("rsi")
    if rsi_val is not None:
        if rsi_val < 30:
            score += 0.25
            reasons.append(f"RSI oversold ({rsi_val:.0f})")
        elif rsi_val < 45:
            score += 0.10
            reasons.append(f"RSI leaning bullish ({rsi_val:.0f})")
        elif rsi_val > 70:
            score -= 0.25
            reasons.append(f"RSI overbought ({rsi_val:.0f})")
        elif rsi_val > 55:
            score -= 0.05
            reasons.append(f"RSI elevated ({rsi_val:.0f})")

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
            reasons.append("Below lower BB (oversold)")
        elif close > bb_upper:
            score -= 0.15
            reasons.append("Above upper BB")
        elif bb_middle is not None and close > bb_middle:
            score += 0.05
            reasons.append("Above BB midline")

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
            reasons.append(f"Williams %R overbought ({wr:.0f})")

    ema_9 = indicators.get("ema_9")
    ema_21 = indicators.get("ema_21")
    if ema_9 is not None and ema_21 is not None:
        if ema_9 > ema_21:
            score += 0.15
            reasons.append("EMA 9>21 bullish cross")
        else:
            score -= 0.10
            reasons.append("EMA 9<21 bearish")

    mom_5 = indicators.get("momentum_5")
    mom_10 = indicators.get("momentum_10")
    if mom_5 is not None:
        if mom_5 > 0.02:
            score += 0.10
            reasons.append(f"5-bar momentum +{mom_5:.1%}")
        elif mom_5 < -0.02:
            score -= 0.10
            reasons.append(f"5-bar momentum {mom_5:.1%}")
    if mom_10 is not None:
        if mom_10 > 0.03:
            score += 0.10
            reasons.append(f"10-bar trend +{mom_10:.1%}")
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
            reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")

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

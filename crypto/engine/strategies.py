"""Aggressive trading strategies — each returns a signal dict for a single pair."""

from __future__ import annotations

from typing import Any


def momentum_breakout(bars: list[dict], indicators: dict) -> dict[str, Any]:
    """Buy on price breaking above recent high with volume confirmation.
    Aggressive: uses 10-bar lookback instead of 20. Sells on breakdown below 10-bar low."""
    if len(bars) < 12:
        return {"signal": "neutral", "score": 0, "confidence": 0, "name": "momentum_breakout"}

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    high_10 = max(highs[-11:-1])
    low_10 = min(lows[-11:-1])
    current = closes[-1]
    prev = closes[-2]

    vol_avg = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
    vol_now = volumes[-1]
    vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1

    score = 0.0
    reasons = []

    if current > high_10 and prev <= high_10:
        score += 0.6
        reasons.append(f"Breakout above 10-bar high ${high_10:,.2f}")
        if vol_ratio > 1.5:
            score += 0.25
            reasons.append(f"Volume confirms ({vol_ratio:.1f}x avg)")
    elif current > high_10:
        score += 0.3
        reasons.append(f"Holding above 10-bar high")
    elif current < low_10:
        score -= 0.6
        reasons.append(f"Breakdown below 10-bar low ${low_10:,.2f}")

    ema_9 = indicators.get("ema_9")
    ema_21 = indicators.get("ema_21")
    if ema_9 and ema_21 and ema_9 > ema_21:
        score += 0.15
        reasons.append("EMA trend aligned")

    score = max(-1.0, min(1.0, score))
    conf = min(1.0, abs(score) * 1.3 + 0.1)

    return {
        "signal": "buy" if score > 0.2 else ("sell" if score < -0.2 else "neutral"),
        "score": score,
        "confidence": conf,
        "name": "momentum_breakout",
        "reasons": reasons,
    }


def mean_reversion(bars: list[dict], indicators: dict) -> dict[str, Any]:
    """Buy at oversold extremes (BB lower + RSI < 35), sell at overbought.
    Aggressive: uses 1.5x BB width instead of 2x for faster trigger."""
    if len(bars) < 20:
        return {"signal": "neutral", "score": 0, "confidence": 0, "name": "mean_reversion"}

    closes = [b["close"] for b in bars]
    current = closes[-1]

    sma_20 = sum(closes[-20:]) / 20
    std_20 = (sum((c - sma_20) ** 2 for c in closes[-20:]) / 20) ** 0.5
    bb_lower_tight = sma_20 - 1.5 * std_20
    bb_upper_tight = sma_20 + 1.5 * std_20

    rsi = indicators.get("rsi")
    wr = indicators.get("williams_r")

    score = 0.0
    reasons = []

    if current < bb_lower_tight:
        score += 0.4
        reasons.append(f"Below tight BB lower (1.5σ)")
        if rsi is not None and rsi < 35:
            score += 0.3
            reasons.append(f"RSI oversold ({rsi:.0f})")
        if wr is not None and wr < -85:
            score += 0.2
            reasons.append(f"Williams %R extreme ({wr:.0f})")
    elif current > bb_upper_tight:
        score -= 0.4
        reasons.append(f"Above tight BB upper (1.5σ)")
        if rsi is not None and rsi > 65:
            score -= 0.3
            reasons.append(f"RSI overbought ({rsi:.0f})")

    distance_from_mean = (current - sma_20) / sma_20 if sma_20 > 0 else 0
    if distance_from_mean < -0.02:
        score += 0.15
        reasons.append(f"Price {distance_from_mean:.1%} below mean")
    elif distance_from_mean > 0.02:
        score -= 0.1

    score = max(-1.0, min(1.0, score))
    conf = min(1.0, abs(score) * 1.4 + 0.1)

    return {
        "signal": "buy" if score > 0.2 else ("sell" if score < -0.2 else "neutral"),
        "score": score,
        "confidence": conf,
        "name": "mean_reversion",
        "reasons": reasons,
    }


def scalp_micro(bars: list[dict], indicators: dict) -> dict[str, Any]:
    """Ultra-short-term: buy on 3-bar pullback in uptrend, sell on 3-bar rally in downtrend.
    Designed for high-frequency entry/exit within minutes."""
    if len(bars) < 10:
        return {"signal": "neutral", "score": 0, "confidence": 0, "name": "scalp_micro"}

    closes = [b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]

    trend_up = closes[-1] > closes[-7]
    last_3_down = all(closes[-(i + 1)] <= closes[-(i + 2)] for i in range(2))
    last_3_up = all(closes[-(i + 1)] >= closes[-(i + 2)] for i in range(2))

    macd_hist = indicators.get("macd_hist")
    vwap = indicators.get("vwap")
    current = closes[-1]

    score = 0.0
    reasons = []

    if trend_up and last_3_down:
        score += 0.5
        reasons.append("3-bar pullback in uptrend")
        if vwap and current < vwap:
            score += 0.2
            reasons.append("Pullback to VWAP")
        if macd_hist and macd_hist > 0:
            score += 0.15
            reasons.append("MACD still bullish")
    elif not trend_up and last_3_up:
        score -= 0.5
        reasons.append("3-bar rally in downtrend (sell signal)")

    vol = indicators.get("volume")
    vol_sma = indicators.get("volume_sma")
    if vol and vol_sma and vol_sma > 0 and vol / vol_sma > 1.3:
        score *= 1.2
        reasons.append(f"Volume confirms ({vol / vol_sma:.1f}x)")

    score = max(-1.0, min(1.0, score))
    conf = min(1.0, abs(score) * 1.2 + 0.15)

    return {
        "signal": "buy" if score > 0.15 else ("sell" if score < -0.15 else "neutral"),
        "score": score,
        "confidence": conf,
        "name": "scalp_micro",
        "reasons": reasons,
    }


def trend_rider(bars: list[dict], indicators: dict) -> dict[str, Any]:
    """Ride strong trends using EMA stack + ADX proxy (momentum magnitude).
    Buy when EMA 9 > 21 with strong momentum, hold until reversal."""
    if len(bars) < 25:
        return {"signal": "neutral", "score": 0, "confidence": 0, "name": "trend_rider"}

    closes = [b["close"] for b in bars]
    current = closes[-1]

    ema_9 = indicators.get("ema_9")
    ema_21 = indicators.get("ema_21")
    mom_5 = indicators.get("momentum_5")
    mom_10 = indicators.get("momentum_10")
    macd_hist = indicators.get("macd_hist")

    score = 0.0
    reasons = []

    if ema_9 and ema_21:
        if ema_9 > ema_21:
            spread_pct = (ema_9 - ema_21) / ema_21 if ema_21 > 0 else 0
            score += 0.3
            reasons.append(f"EMA 9>21 (spread {spread_pct:.2%})")
            if spread_pct > 0.005:
                score += 0.15
                reasons.append("Strong EMA separation")
        else:
            score -= 0.3
            reasons.append("EMA 9<21 bearish")

    if mom_5 is not None and mom_10 is not None:
        if mom_5 > 0.01 and mom_10 > 0.01:
            score += 0.25
            reasons.append(f"Dual momentum +{mom_5:.1%}/{mom_10:.1%}")
        elif mom_5 < -0.01 and mom_10 < -0.01:
            score -= 0.25
            reasons.append("Dual negative momentum")

    if macd_hist is not None:
        if macd_hist > 0:
            score += 0.15
            reasons.append("MACD positive")
        else:
            score -= 0.15

    score = max(-1.0, min(1.0, score))
    conf = min(1.0, abs(score) * 1.3 + 0.1)

    return {
        "signal": "buy" if score > 0.2 else ("sell" if score < -0.2 else "neutral"),
        "score": score,
        "confidence": conf,
        "name": "trend_rider",
        "reasons": reasons,
    }


ALL_STRATEGIES = {
    "momentum_breakout": momentum_breakout,
    "mean_reversion": mean_reversion,
    "scalp_micro": scalp_micro,
    "trend_rider": trend_rider,
}


def run_all_strategies(bars: list[dict], indicators: dict) -> list[dict[str, Any]]:
    """Run every strategy and return their signals."""
    return [fn(bars, indicators) for fn in ALL_STRATEGIES.values()]

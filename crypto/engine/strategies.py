"""Institutional-grade trading strategies — regime-aware, multi-signal.

Each strategy returns a signal dict with: signal, score, confidence, name, reasons, regime.
"""

from __future__ import annotations

from typing import Any


def volatility_breakout(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Keltner Channel breakout with ATR expansion confirmation.

    Preferred regime: volatile. Enters when price closes outside KC and
    ATR is expanding relative to its 20-period average.
    """
    if len(bars) < 25:
        return _neutral("volatility_breakout")

    close = indicators.get("close", 0)
    kc_upper = indicators.get("kc_upper")
    kc_lower = indicators.get("kc_lower")
    atr_val = indicators.get("atr")
    atr_avg = indicators.get("atr_sma_20")

    if not all([close, kc_upper, kc_lower, atr_val]):
        return _neutral("volatility_breakout")

    atr_expanding = atr_avg and atr_val > atr_avg * 1.3

    score = 0.0
    reasons = []

    if close > kc_upper:
        score += 0.5
        reasons.append(f"Breakout above KC upper ${kc_upper:,.2f}")
        if atr_expanding:
            score += 0.3
            reasons.append(f"ATR expanding ({atr_val / atr_avg:.1f}x avg)")
    elif close < kc_lower:
        score -= 0.5
        reasons.append(f"Breakdown below KC lower ${kc_lower:,.2f}")
        if atr_expanding:
            score -= 0.3
            reasons.append("ATR expanding (bearish)")

    volumes = [b.get("volume", 0) for b in bars]
    vol_avg = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
    if vol_avg > 0 and volumes[-1] > vol_avg * 1.5:
        score *= 1.2
        reasons.append("Volume confirms")

    return _build("volatility_breakout", score, reasons, "volatile")


def mean_reversion_zscore(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Z-score mean reversion — buy at z < -2, sell at z > 2.

    Preferred regime: mean_reverting. Uses price distance from 50-bar SMA.
    """
    if len(bars) < 55:
        return _neutral("mean_reversion_zscore")

    closes = [b["close"] for b in bars]
    current = closes[-1]
    window = closes[-50:]
    mean = sum(window) / len(window)
    std = (sum((c - mean) ** 2 for c in window) / len(window)) ** 0.5

    if std == 0:
        return _neutral("mean_reversion_zscore")

    z = (current - mean) / std

    rsi_val = indicators.get("rsi")
    wr = indicators.get("williams_r")

    score = 0.0
    reasons = [f"Z-score: {z:.2f}"]

    if z < -2.0:
        score += 0.5
        reasons.append("Extreme oversold (z < -2)")
        if rsi_val and rsi_val < 30:
            score += 0.2
            reasons.append(f"RSI confirms ({rsi_val:.0f})")
        if wr and wr < -90:
            score += 0.15
            reasons.append(f"Williams %R extreme ({wr:.0f})")
    elif z < -1.5:
        score += 0.3
        reasons.append("Moderately oversold")
    elif z > 2.0:
        score -= 0.5
        reasons.append("Extreme overbought (z > 2)")
        if rsi_val and rsi_val > 70:
            score -= 0.2
            reasons.append(f"RSI confirms ({rsi_val:.0f})")
    elif z > 1.5:
        score -= 0.3
        reasons.append("Moderately overbought")

    return _build("mean_reversion_zscore", score, reasons, "mean_reverting")


def momentum_cascade(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Multi-factor momentum — requires 3/4 factors aligned.

    Preferred regime: trending_up. Factors: price momentum (5/10/20),
    volume momentum, RSI rate-of-change.
    """
    if len(bars) < 25:
        return _neutral("momentum_cascade")

    mom_5 = indicators.get("momentum_5") or 0
    mom_10 = indicators.get("momentum_10") or 0
    mom_20 = indicators.get("momentum_20") or 0
    vol_mom = indicators.get("volume_momentum") or 0
    rsi_val = indicators.get("rsi") or 50

    bull_factors = 0
    bear_factors = 0
    reasons = []

    if mom_5 > 0.005:
        bull_factors += 1
        reasons.append(f"5-bar mom +{mom_5:.1%}")
    elif mom_5 < -0.005:
        bear_factors += 1

    if mom_10 > 0.01:
        bull_factors += 1
        reasons.append(f"10-bar mom +{mom_10:.1%}")
    elif mom_10 < -0.01:
        bear_factors += 1

    if mom_20 > 0.02:
        bull_factors += 1
        reasons.append(f"20-bar trend +{mom_20:.1%}")
    elif mom_20 < -0.02:
        bear_factors += 1

    if vol_mom > 0.2:
        bull_factors += 1
        reasons.append("Volume rising")
    elif vol_mom < -0.2:
        bear_factors += 1

    score = 0.0
    if bull_factors >= 3:
        score = 0.3 + 0.15 * bull_factors
        reasons.append(f"{bull_factors}/4 bull factors aligned")
    elif bear_factors >= 3:
        score = -(0.3 + 0.15 * bear_factors)
        reasons.append(f"{bear_factors}/4 bear factors aligned")

    if rsi_val > 50 and score > 0:
        score += 0.1
    elif rsi_val < 50 and score < 0:
        score -= 0.1

    return _build("momentum_cascade", score, reasons, "trending_up")


def liquidity_grab(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Stop hunt detection — price spikes through S/R then reverses.

    Preferred regime: any. Detects wicks through recent highs/lows followed
    by reversal, indicating institutional liquidity sweeps.
    """
    if len(bars) < 20:
        return _neutral("liquidity_grab")

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    current = closes[-1]

    recent_high = max(highs[-15:-1])
    recent_low = min(lows[-15:-1])

    last_high = highs[-1]
    last_low = lows[-1]

    score = 0.0
    reasons = []

    if last_high > recent_high and current < recent_high:
        wick_pct = (last_high - current) / current * 100 if current > 0 else 0
        if wick_pct > 0.3:
            score -= 0.5
            reasons.append(f"Bear liquidity grab — wick {wick_pct:.1f}% above resistance then reversal")

    if last_low < recent_low and current > recent_low:
        wick_pct = (current - last_low) / current * 100 if current > 0 else 0
        if wick_pct > 0.3:
            score += 0.5
            reasons.append(f"Bull liquidity grab — wick {wick_pct:.1f}% below support then reversal")

    vol = indicators.get("volume", 0)
    vol_sma = indicators.get("volume_sma", 0)
    if vol and vol_sma and vol_sma > 0 and vol / vol_sma > 2.0:
        score *= 1.3
        reasons.append("High volume confirms sweep")

    return _build("liquidity_grab", score, reasons, "any")


def vwap_reversion(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Institutional VWAP mean-reversion — buy at -1.5 stddev, sell at +1.5.

    Preferred regime: mean_reverting.
    """
    if len(bars) < 30:
        return _neutral("vwap_reversion")

    close = indicators.get("close", 0)
    vwap_val = indicators.get("vwap")

    if not close or not vwap_val or vwap_val <= 0:
        return _neutral("vwap_reversion")

    closes = [b["close"] for b in bars[-30:]]
    vwap_distances = [(c - vwap_val) / vwap_val for c in closes]
    std_dist = (sum(d ** 2 for d in vwap_distances) / len(vwap_distances)) ** 0.5

    if std_dist == 0:
        return _neutral("vwap_reversion")

    current_dist = (close - vwap_val) / vwap_val
    z_from_vwap = current_dist / std_dist

    score = 0.0
    reasons = [f"VWAP distance: {z_from_vwap:.2f}σ"]

    if z_from_vwap < -1.5:
        score += 0.5
        reasons.append("Below VWAP -1.5σ (institutional buy zone)")
    elif z_from_vwap < -1.0:
        score += 0.25
        reasons.append("Approaching VWAP buy zone")
    elif z_from_vwap > 1.5:
        score -= 0.5
        reasons.append("Above VWAP +1.5σ (institutional sell zone)")
    elif z_from_vwap > 1.0:
        score -= 0.25
        reasons.append("Approaching VWAP sell zone")

    return _build("vwap_reversion", score, reasons, "mean_reverting")


def ema_ribbon(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """EMA ribbon (8/13/21/34/55) — aligned = strong trend, compressed = breakout imminent.

    Preferred regime: trending.
    """
    if len(bars) < 60:
        return _neutral("ema_ribbon")

    emas = [
        indicators.get("ema_8"),
        indicators.get("ema_13"),
        indicators.get("ema_21"),
        indicators.get("ema_34"),
        indicators.get("ema_55"),
    ]

    if not all(emas):
        return _neutral("ema_ribbon")

    score = 0.0
    reasons = []

    bullish_order = all(emas[i] >= emas[i + 1] for i in range(len(emas) - 1))
    bearish_order = all(emas[i] <= emas[i + 1] for i in range(len(emas) - 1))

    if bullish_order:
        score += 0.5
        reasons.append("Full bullish EMA ribbon alignment")
    elif bearish_order:
        score -= 0.5
        reasons.append("Full bearish EMA ribbon alignment")

    ribbon_width = (max(emas) - min(emas)) / emas[2] if emas[2] > 0 else 0
    if ribbon_width < 0.005:
        reasons.append(f"Ribbon compressed ({ribbon_width:.3%}) — breakout imminent")
        score *= 0.5  # reduce until direction clear
    elif ribbon_width > 0.02:
        reasons.append(f"Wide ribbon ({ribbon_width:.3%}) — strong trend")
        score *= 1.2

    macd_hist = indicators.get("macd_hist")
    if macd_hist and macd_hist > 0 and score > 0:
        score += 0.15
    elif macd_hist and macd_hist < 0 and score < 0:
        score -= 0.15

    return _build("ema_ribbon", score, reasons, "trending_up")


def order_flow_momentum(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Pure microstructure strategy — enters on strong order flow signals.

    Preferred regime: any. Requires microstructure data passed via kwargs.
    """
    micro = kw.get("microstructure", {})
    if not micro:
        return _neutral("order_flow_momentum")

    imbalance = micro.get("imbalance", 0)
    flow = micro.get("flow", 0)
    vpin = micro.get("vpin", 0.5)

    score = 0.0
    reasons = []

    if imbalance > 0.4 and flow > 0.3:
        score += 0.6
        reasons.append(f"Strong buy flow (imb={imbalance:.2f}, flow={flow:.2f})")
    elif imbalance < -0.4 and flow < -0.3:
        score -= 0.6
        reasons.append(f"Strong sell flow (imb={imbalance:.2f}, flow={flow:.2f})")
    elif imbalance > 0.2:
        score += 0.2
    elif imbalance < -0.2:
        score -= 0.2

    if vpin > 0.7:
        score *= 1.3
        reasons.append(f"High VPIN ({vpin:.2f}) — informed trading detected")

    return _build("order_flow_momentum", score, reasons, "any")


def funding_rate_arb(bars: list[dict], indicators: dict, **kw) -> dict[str, Any]:
    """Contrarian funding rate — trade against extreme funding.

    Preferred regime: any. When funding > 0.03%, longs are overleveraged
    (sell bias). When funding < -0.03%, shorts are overleveraged (buy bias).
    """
    onchain = kw.get("onchain", {})
    funding = onchain.get("btc_funding", 0)

    if funding == 0:
        return _neutral("funding_rate_arb")

    score = 0.0
    reasons = [f"Funding rate: {funding:.4%}"]

    if funding > 0.0005:
        score -= 0.5
        reasons.append("Extreme positive funding — longs overleveraged")
    elif funding > 0.0003:
        score -= 0.3
        reasons.append("High positive funding — lean short")
    elif funding < -0.0005:
        score += 0.5
        reasons.append("Extreme negative funding — shorts overleveraged")
    elif funding < -0.0003:
        score += 0.3
        reasons.append("High negative funding — lean long")

    return _build("funding_rate_arb", score, reasons, "any")


# ── Helpers ──────────────────────────────────────────────────────────

def _neutral(name: str) -> dict[str, Any]:
    return {"signal": "neutral", "score": 0, "confidence": 0, "name": name, "reasons": [], "regime": "any"}


def _build(name: str, score: float, reasons: list[str], regime: str) -> dict[str, Any]:
    score = max(-1.0, min(1.0, score))
    conf = min(1.0, abs(score) * 1.3 + 0.1)
    signal = "buy" if score > 0.2 else ("sell" if score < -0.2 else "neutral")
    return {
        "signal": signal,
        "score": round(score, 4),
        "confidence": round(conf, 4),
        "name": name,
        "reasons": reasons,
        "regime": regime,
    }


ALL_STRATEGIES = {
    "volatility_breakout": volatility_breakout,
    "mean_reversion_zscore": mean_reversion_zscore,
    "momentum_cascade": momentum_cascade,
    "liquidity_grab": liquidity_grab,
    "vwap_reversion": vwap_reversion,
    "ema_ribbon": ema_ribbon,
    "order_flow_momentum": order_flow_momentum,
    "funding_rate_arb": funding_rate_arb,
}

REGIME_STRATEGY_WEIGHTS = {
    "trending_up": {"momentum_cascade": 2.0, "ema_ribbon": 2.0, "volatility_breakout": 1.5},
    "trending_down": {"momentum_cascade": 2.0, "ema_ribbon": 2.0, "volatility_breakout": 1.5},
    "mean_reverting": {"mean_reversion_zscore": 2.0, "vwap_reversion": 2.0, "liquidity_grab": 1.5},
    "volatile": {"volatility_breakout": 2.0, "liquidity_grab": 1.5, "order_flow_momentum": 1.5},
}


def run_all_strategies(
    bars: list[dict],
    indicators: dict,
    regime: str | None = None,
    microstructure: dict | None = None,
    onchain: dict | None = None,
) -> list[dict[str, Any]]:
    """Run every strategy and return their signals, with optional regime filtering."""
    results = []
    regime_weights = REGIME_STRATEGY_WEIGHTS.get(regime, {}) if regime else {}

    for name, fn in ALL_STRATEGIES.items():
        sig = fn(bars, indicators, microstructure=microstructure or {}, onchain=onchain or {})
        regime_mult = regime_weights.get(name, 1.0)
        if regime_mult != 1.0:
            sig["score"] = round(max(-1.0, min(1.0, sig["score"] * regime_mult)), 4)
            sig["confidence"] = round(min(1.0, sig["confidence"] * (1 + (regime_mult - 1) * 0.3)), 4)
            if sig["score"] > 0.2:
                sig["signal"] = "buy"
            elif sig["score"] < -0.2:
                sig["signal"] = "sell"
            else:
                sig["signal"] = "neutral"
        results.append(sig)

    return results

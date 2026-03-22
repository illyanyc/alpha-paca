"""Market regime detection — classifies current state as trending, ranging, or volatile.

Uses a simplified HMM-inspired approach with realized volatility, returns
autocorrelation, and Hurst exponent features. Falls back to a rule-based
classifier when hmmlearn is not available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class Regime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    MEAN_REVERTING = "mean_reverting"
    VOLATILE = "volatile"


REGIME_LABELS = {
    Regime.TRENDING_UP: "TREND-UP",
    Regime.TRENDING_DOWN: "TREND-DOWN",
    Regime.MEAN_REVERTING: "MEAN-REVERT",
    Regime.VOLATILE: "VOLATILE",
}


@dataclass
class RegimeState:
    regime: Regime
    confidence: float
    label: str
    features: dict[str, float]


def _compute_returns(closes: list[float]) -> list[float]:
    return [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def _realized_volatility(returns: list[float], window: int = 24) -> float:
    if len(returns) < window:
        return 0.0
    recent = returns[-window:]
    mean_r = sum(recent) / len(recent)
    var = sum((r - mean_r) ** 2 for r in recent) / len(recent)
    return math.sqrt(var) * math.sqrt(365 * 24)  # annualized from hourly


def _returns_autocorrelation(returns: list[float], lag: int = 1) -> float:
    """Lag-1 autocorrelation — positive = trending, negative = mean-reverting."""
    if len(returns) < lag + 10:
        return 0.0
    n = len(returns) - lag
    mean_r = sum(returns[:n]) / n
    mean_l = sum(returns[lag:]) / n

    cov = sum((returns[i] - mean_r) * (returns[i + lag] - mean_l) for i in range(n)) / n
    var1 = sum((returns[i] - mean_r) ** 2 for i in range(n)) / n
    var2 = sum((returns[i + lag] - mean_l) ** 2 for i in range(n)) / n

    denom = math.sqrt(var1 * var2)
    return cov / denom if denom > 0 else 0.0


def _hurst_exponent(returns: list[float], max_lag: int = 20) -> float:
    """Simplified Hurst exponent via rescaled range (R/S) method.

    H > 0.5 = trending (persistent), H < 0.5 = mean-reverting, H ~ 0.5 = random walk.
    """
    if len(returns) < max_lag * 2:
        return 0.5

    rs_values: list[tuple[float, float]] = []
    for lag in range(10, max_lag + 1):
        chunks = [returns[i:i + lag] for i in range(0, len(returns) - lag + 1, lag)]
        chunks = [c for c in chunks if len(c) == lag]
        if not chunks:
            continue

        rs_for_lag: list[float] = []
        for chunk in chunks:
            mean_c = sum(chunk) / len(chunk)
            deviations = [sum(chunk[:j + 1]) - mean_c * (j + 1) for j in range(len(chunk))]
            r = max(deviations) - min(deviations)
            s = math.sqrt(sum((x - mean_c) ** 2 for x in chunk) / len(chunk))
            if s > 0:
                rs_for_lag.append(r / s)

        if rs_for_lag:
            avg_rs = sum(rs_for_lag) / len(rs_for_lag)
            if avg_rs > 0:
                rs_values.append((math.log(lag), math.log(avg_rs)))

    if len(rs_values) < 3:
        return 0.5

    xs, ys = zip(*rs_values)
    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.5
    return (n * sum_xy - sum_x * sum_y) / denom


def _trend_strength(closes: list[float], window: int = 48) -> float:
    """Directional trend strength over the window — positive = up, negative = down."""
    if len(closes) < window:
        return 0.0
    start = sum(closes[-window:-window + 5]) / 5 if window > 5 else closes[-window]
    end = sum(closes[-5:]) / 5
    return (end - start) / start if start > 0 else 0.0


def detect_regime(hourly_closes: list[float]) -> RegimeState:
    """Classify current market regime from hourly close prices.

    Requires at least 72 hourly bars (3 days) for meaningful classification.
    """
    if len(hourly_closes) < 48:
        return RegimeState(
            regime=Regime.VOLATILE,
            confidence=0.3,
            label=REGIME_LABELS[Regime.VOLATILE],
            features={},
        )

    returns = _compute_returns(hourly_closes)
    vol = _realized_volatility(returns)
    autocorr = _returns_autocorrelation(returns)
    hurst = _hurst_exponent(returns)
    trend = _trend_strength(hourly_closes)

    features = {
        "realized_vol": round(vol, 4),
        "autocorrelation": round(autocorr, 4),
        "hurst_exponent": round(hurst, 4),
        "trend_strength": round(trend, 4),
    }

    vol_high = vol > 0.8
    trending = hurst > 0.55 and abs(autocorr) > 0.05
    mean_rev = hurst < 0.45 and autocorr < -0.05

    if vol_high and abs(trend) < 0.02:
        regime = Regime.VOLATILE
        confidence = min(1.0, vol / 1.2)
    elif trending and trend > 0.01:
        regime = Regime.TRENDING_UP
        confidence = min(1.0, 0.5 + hurst * 0.5 + abs(trend) * 5)
    elif trending and trend < -0.01:
        regime = Regime.TRENDING_DOWN
        confidence = min(1.0, 0.5 + hurst * 0.5 + abs(trend) * 5)
    elif mean_rev:
        regime = Regime.MEAN_REVERTING
        confidence = min(1.0, 0.5 + (0.5 - hurst) * 2 + abs(autocorr) * 2)
    elif abs(trend) > 0.03:
        regime = Regime.TRENDING_UP if trend > 0 else Regime.TRENDING_DOWN
        confidence = min(1.0, 0.4 + abs(trend) * 5)
    else:
        regime = Regime.MEAN_REVERTING
        confidence = 0.4

    confidence = round(max(0.2, min(1.0, confidence)), 2)

    return RegimeState(
        regime=regime,
        confidence=confidence,
        label=REGIME_LABELS[regime],
        features=features,
    )

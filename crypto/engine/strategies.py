"""Adaptive Momentum + News Alpha strategy — deterministic 3-layer composite scoring.

Replaces the previous 8-strategy system with a single research-backed approach:
  - Technical Momentum (50%): 4H MACD(8-17-9), RSI(5)>50, EMA(8/21), VWAP, daily MACD filter
  - News/Sentiment (30%): LLM-classified news score, Fear & Greed, funding rate
  - On-Chain/Microstructure (20%): exchange flows, OI, book imbalance

Composite score range: -100 to +100.  BUY threshold: >40.  EXIT threshold: <-20.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of a composite score computation."""
    technical: float = 0.0
    sentiment: float = 0.0
    onchain: float = 0.0
    composite: float = 0.0
    tech_conditions: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


class AdaptiveMomentumStrategy:
    """Deterministic 4H momentum strategy with 3-layer composite scoring.

    All scoring is rule-based — no LLM calls for entry/exit decisions.
    LLM is only used upstream for news sentiment classification.
    """

    TECH_WEIGHT = 0.50
    SENT_WEIGHT = 0.30
    ONCHAIN_WEIGHT = 0.20

    def compute_technical_score(
        self,
        indicators_4h: dict[str, Any],
        indicators_daily: dict[str, Any] | None = None,
    ) -> tuple[float, dict[str, bool]]:
        """Compute technical momentum score from 4H + daily indicators.

        Returns (score in 0..100, dict of condition booleans).
        Score is sum of individual condition points; all 6 conditions
        must be TRUE simultaneously for maximum confidence entry.
        """
        if not indicators_4h:
            return 0.0, {}

        conditions: dict[str, bool] = {}
        score = 0.0

        macd_4h_line = indicators_4h.get("macd_4h_line")
        macd_4h_signal = indicators_4h.get("macd_4h_signal")
        macd_4h_hist = indicators_4h.get("macd_4h_hist")
        macd_4h_bullish_cross = indicators_4h.get("macd_4h_bullish_cross", False)

        if macd_4h_line is not None and macd_4h_signal is not None:
            macd_above_signal = macd_4h_line > macd_4h_signal
            macd_above_zero = macd_4h_line > 0
            conditions["macd_bullish"] = macd_above_signal and macd_above_zero
            if conditions["macd_bullish"]:
                score += 25
            elif macd_above_signal:
                score += 10
            if macd_4h_bullish_cross:
                score += 5
        else:
            conditions["macd_bullish"] = False

        rsi_5 = indicators_4h.get("rsi_5")
        if rsi_5 is not None:
            conditions["rsi_momentum"] = rsi_5 > 50
            if rsi_5 > 70:
                score += 20
            elif rsi_5 > 50:
                score += 15
            elif rsi_5 > 40:
                score += 5
        else:
            conditions["rsi_momentum"] = False

        ema_8 = indicators_4h.get("ema_8")
        ema_21 = indicators_4h.get("ema_21")
        if ema_8 is not None and ema_21 is not None:
            conditions["ema_cross"] = ema_8 > ema_21
            if conditions["ema_cross"]:
                score += 15
        else:
            conditions["ema_cross"] = False

        close = indicators_4h.get("close")
        vwap_val = indicators_4h.get("vwap")
        if close is not None and vwap_val is not None and vwap_val > 0:
            conditions["above_vwap"] = close > vwap_val
            if conditions["above_vwap"]:
                score += 10
        else:
            conditions["above_vwap"] = False

        vol_ratio = indicators_4h.get("vol_ratio_20")
        if vol_ratio is not None:
            conditions["volume_confirm"] = vol_ratio > 1.2
            if vol_ratio > 2.0:
                score += 15
            elif vol_ratio > 1.5:
                score += 12
            elif vol_ratio > 1.2:
                score += 10
        else:
            conditions["volume_confirm"] = False

        if indicators_daily:
            daily_macd_line = indicators_daily.get("macd_line")
            daily_macd_signal = indicators_daily.get("macd_signal")
            if daily_macd_line is not None and daily_macd_signal is not None:
                conditions["daily_trend_up"] = daily_macd_line > daily_macd_signal
                if conditions["daily_trend_up"]:
                    score += 25
            else:
                conditions["daily_trend_up"] = False
        else:
            conditions["daily_trend_up"] = False

        score = max(0.0, min(100.0, score))
        return score, conditions

    def compute_sentiment_score(
        self,
        news_data: dict[str, Any] | None = None,
        onchain_data: dict[str, Any] | None = None,
    ) -> float:
        """Compute news/sentiment score.  Range: -100 to +100.

        Components (weighted within this 30% layer):
          - News LLM overall_score: scaled to [-50, +50]
          - Fear & Greed contrarian: [-30, +30]
          - Funding rate contrarian: [-20, +20]
        """
        score = 0.0

        if news_data:
            raw_news_score = news_data.get("overall_score", 0.0)
            score += raw_news_score * 50

        if onchain_data:
            fg = onchain_data.get("fear_greed_index", 50)
            if isinstance(fg, (int, float)):
                if fg < 20:
                    score += 30
                elif fg < 35:
                    score += 15
                elif fg > 80:
                    score -= 30
                elif fg > 65:
                    score -= 15

            btc_fr = onchain_data.get("btc_funding_rate", 0.0)
            if isinstance(btc_fr, (int, float)):
                if btc_fr < -0.0003:
                    score += 20
                elif btc_fr < -0.0001:
                    score += 10
                elif btc_fr > 0.0005:
                    score -= 20
                elif btc_fr > 0.0003:
                    score -= 15
                elif btc_fr > 0.0001:
                    score -= 5

        return max(-100.0, min(100.0, score))

    def compute_onchain_score(
        self,
        onchain_data: dict[str, Any] | None = None,
        microstructure: dict[str, Any] | None = None,
    ) -> float:
        """Compute on-chain / microstructure score.  Range: -100 to +100.

        Components:
          - Exchange flow signal: [-30, +30]
          - OI + funding combo: [-30, +30]
          - Order book imbalance: [-40, +40]
        """
        score = 0.0

        if onchain_data:
            flow_signal = onchain_data.get("exchange_flow_signal", "neutral")
            if flow_signal == "outflow":
                score += 30
            elif flow_signal == "inflow":
                score -= 30
            elif flow_signal == "slight_outflow":
                score += 15
            elif flow_signal == "slight_inflow":
                score -= 15

            oi_rising = onchain_data.get("oi_rising", False)
            btc_fr = onchain_data.get("btc_funding_rate", 0.0)
            if isinstance(btc_fr, (int, float)):
                if oi_rising and btc_fr < -0.0001:
                    score += 30
                elif oi_rising and btc_fr > 0.001:
                    score -= 30

            liquidation_cascade = onchain_data.get("liquidation_cascade", False)
            if liquidation_cascade:
                score -= 20

        if microstructure:
            imbalance = microstructure.get("imbalance", 0.0)
            if isinstance(imbalance, (int, float)):
                if imbalance > 0.5:
                    score += 40
                elif imbalance > 0.3:
                    score += 25
                elif imbalance > 0.15:
                    score += 10
                elif imbalance < -0.5:
                    score -= 40
                elif imbalance < -0.3:
                    score -= 25
                elif imbalance < -0.15:
                    score -= 10

        return max(-100.0, min(100.0, score))

    def composite_score(
        self,
        tech_score: float,
        sent_score: float,
        onchain_score: float,
    ) -> float:
        """Weighted composite.  Range: -100 to +100."""
        raw = (
            tech_score * self.TECH_WEIGHT
            + sent_score * self.SENT_WEIGHT
            + onchain_score * self.ONCHAIN_WEIGHT
        )
        return max(-100.0, min(100.0, raw))

    def evaluate(
        self,
        indicators_4h: dict[str, Any],
        indicators_daily: dict[str, Any] | None = None,
        news_data: dict[str, Any] | None = None,
        onchain_data: dict[str, Any] | None = None,
        microstructure: dict[str, Any] | None = None,
        buy_threshold: int = 40,
        exit_threshold: int = -20,
    ) -> ScoreBreakdown:
        """Full evaluation pipeline — returns score breakdown and action hints."""
        tech, conditions = self.compute_technical_score(indicators_4h, indicators_daily)
        sent = self.compute_sentiment_score(news_data, onchain_data)
        onchain = self.compute_onchain_score(onchain_data, microstructure)
        comp = self.composite_score(tech, sent, onchain)

        reasons: list[str] = []
        if tech >= 60:
            reasons.append(f"Strong technical momentum ({tech:.0f}/100)")
        elif tech >= 30:
            reasons.append(f"Moderate technical ({tech:.0f}/100)")
        else:
            reasons.append(f"Weak technical ({tech:.0f}/100)")

        if sent > 20:
            reasons.append(f"Bullish sentiment ({sent:+.0f})")
        elif sent < -20:
            reasons.append(f"Bearish sentiment ({sent:+.0f})")

        if onchain > 15:
            reasons.append(f"Bullish on-chain ({onchain:+.0f})")
        elif onchain < -15:
            reasons.append(f"Bearish on-chain ({onchain:+.0f})")

        all_tech_met = all(conditions.values()) if conditions else False
        if comp >= buy_threshold and all_tech_met:
            reasons.append("ALL 6 technical conditions met — high-confidence entry")
        elif comp >= buy_threshold:
            met = sum(1 for v in conditions.values() if v)
            reasons.append(f"{met}/{len(conditions)} technical conditions met")

        return ScoreBreakdown(
            technical=round(tech, 2),
            sentiment=round(sent, 2),
            onchain=round(onchain, 2),
            composite=round(comp, 2),
            tech_conditions=conditions,
            reasons=reasons,
        )


# ── Backward-compat exports ─────────────────────────────────────────

def _adaptive_momentum_wrapper(bars, indicators, **kwargs):
    """Legacy function-style wrapper for the strategy."""
    breakdown = _strategy.evaluate(
        indicators_4h=indicators,
        news_data=None,
        onchain_data=kwargs.get("onchain"),
        microstructure=kwargs.get("microstructure"),
    )
    score_normalized = breakdown.composite / 100.0
    if score_normalized > 0.2:
        signal = "buy"
    elif score_normalized < -0.2:
        signal = "sell"
    else:
        signal = "neutral"
    return {
        "signal": signal,
        "score": round(score_normalized, 4),
        "confidence": min(1.0, abs(score_normalized) * 1.5 + 0.1),
        "name": "adaptive_momentum",
    }


ALL_STRATEGIES = {"adaptive_momentum": _adaptive_momentum_wrapper}

_strategy = AdaptiveMomentumStrategy()


def run_all_strategies(
    bars: list[dict],
    indicators: dict,
    regime: str | None = None,
    microstructure: dict | None = None,
    onchain: dict | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper that returns a single strategy result."""
    breakdown = _strategy.evaluate(
        indicators_4h=indicators,
        news_data=None,
        onchain_data=onchain,
        microstructure=microstructure,
    )

    score_normalized = breakdown.composite / 100.0
    confidence = min(1.0, abs(score_normalized) * 1.5 + 0.1)
    if score_normalized > 0.2:
        signal = "buy"
    elif score_normalized < -0.2:
        signal = "sell"
    else:
        signal = "neutral"

    return [{
        "signal": signal,
        "score": round(score_normalized, 4),
        "confidence": round(confidence, 4),
        "name": "adaptive_momentum",
        "reasons": breakdown.reasons,
        "regime": regime or "any",
        "tech_score": breakdown.technical,
        "sentiment_score": breakdown.sentiment,
        "onchain_score": breakdown.onchain,
        "composite": breakdown.composite,
    }]

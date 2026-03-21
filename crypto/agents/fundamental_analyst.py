"""FundamentalAnalystAgent — volume spikes, dominance shifts, anomaly detection."""

from __future__ import annotations

import json

import redis.asyncio as aioredis
import structlog

from agents.base import BaseAgent
from config import get_settings
from engine.signals import ComponentSignal, SignalStrength
from services.coinbase_crypto import CoinbaseCryptoService

logger = structlog.get_logger(__name__)

FUND_SIGNAL_KEY = "crypto:signals:fundamental"
FUND_SIGNAL_TTL = 600


class FundamentalAnalystAgent(BaseAgent):
    name = "fundamental_analyst"

    def __init__(self, exchange: CoinbaseCryptoService) -> None:
        super().__init__()
        self._exchange = exchange

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        pairs = settings.crypto.pair_list
        results: dict[str, dict] = {}

        self.think(f"Analyzing volume & price structure for {len(pairs)} pairs...")

        btc_price: float | None = None

        for pair in pairs:
            try:
                bars = self._exchange.get_bars(pair, lookback_minutes=1440)
                if len(bars) < 30:
                    continue

                volumes = [b["volume"] for b in bars]
                closes = [b["close"] for b in bars]

                recent_vol = sum(volumes[-10:]) / max(len(volumes[-10:]), 1)
                avg_vol = sum(volumes) / max(len(volumes), 1)
                vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

                current_price = closes[-1]
                ma_7d = sum(closes[-420:]) / min(len(closes), 420) if len(closes) >= 30 else current_price
                ma_30d = sum(closes) / len(closes) if closes else current_price

                if pair == "BTC/USD":
                    btc_price = current_price

                score = 0.0
                reasons: list[str] = []

                if vol_ratio > 3.0:
                    score += 0.3
                    reasons.append(f"Volume spike {vol_ratio:.1f}x avg")
                elif vol_ratio > 1.5:
                    score += 0.1
                    reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")
                elif vol_ratio < 0.5:
                    score -= 0.1
                    reasons.append("Low volume")

                price_vs_7d = (current_price - ma_7d) / ma_7d if ma_7d > 0 else 0
                if price_vs_7d > 0.05:
                    score += 0.2
                    reasons.append(f"Price +{price_vs_7d:.1%} vs 7d MA")
                elif price_vs_7d < -0.05:
                    score -= 0.2
                    reasons.append(f"Price {price_vs_7d:.1%} vs 7d MA")

                price_vs_30d = (current_price - ma_30d) / ma_30d if ma_30d > 0 else 0
                if price_vs_30d > 0.1:
                    score += 0.15
                    reasons.append(f"Strong uptrend vs 30d")
                elif price_vs_30d < -0.1:
                    score -= 0.15
                    reasons.append(f"Downtrend vs 30d")

                score = max(-1.0, min(1.0, score))
                confidence = min(1.0, abs(score) * 1.5 + 0.1)

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

                results[pair] = {
                    "signal": sig.value,
                    "score": score,
                    "confidence": confidence,
                    "details": "; ".join(reasons),
                    "vol_ratio": vol_ratio,
                    "price_vs_7d_pct": price_vs_7d * 100,
                    "price_vs_30d_pct": price_vs_30d * 100,
                }

            except Exception:
                logger.exception("fundamental_analysis_failed", pair=pair)

        fund_summary = ", ".join(f"{p}: {d['signal']}({d['score']:.2f})" for p, d in results.items())
        self.think(f"Fundamentals: {fund_summary}")

        r = await self._get_redis()
        await r.set(FUND_SIGNAL_KEY, json.dumps(results), ex=FUND_SIGNAL_TTL)

        logger.info("fundamental_analysis_complete", pairs=len(results))
        return results

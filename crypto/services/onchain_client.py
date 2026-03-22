"""On-chain and derivatives data client for crypto-native alpha signals.

Fetches funding rates, open interest, exchange flows, and Fear/Greed index
from free public APIs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
COINGLASS_FUNDING_URL = "https://open-api.coinglass.com/public/v2/funding"
BLOCKCHAIN_EXCHANGE_BALANCE_URL = "https://api.blockchain.info/charts/balance-exchanges"


@dataclass
class OnChainSignals:
    fear_greed_index: int = 50
    fear_greed_label: str = "neutral"
    btc_funding_rate: float = 0.0
    eth_funding_rate: float = 0.0
    signal: str = "neutral"
    score: float = 0.0
    confidence: float = 0.0
    details: dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


async def fetch_fear_greed() -> tuple[int, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()
            entry = data.get("data", [{}])[0]
            value = int(entry.get("value", 50))
            label = entry.get("value_classification", "neutral")
            return value, label
    except Exception as e:
        logger.warning("fear_greed_fetch_failed", error=str(e)[:80])
        return 50, "neutral"


async def fetch_funding_rates() -> dict[str, float]:
    """Fetch BTC and ETH funding rates. Returns {symbol: rate}."""
    rates: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for symbol in ("BTC", "ETH"):
                try:
                    resp = await client.get(
                        COINGLASS_FUNDING_URL,
                        params={"symbol": symbol, "time_type": "all"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        entries = data.get("data", [])
                        if entries and isinstance(entries, list):
                            rate = float(entries[0].get("uMarginList", [{}])[0].get("rate", 0))
                            rates[symbol] = rate
                except Exception:
                    rates[symbol] = 0.0
    except Exception as e:
        logger.warning("funding_rate_fetch_failed", error=str(e)[:80])
    return rates


async def fetch_all_onchain() -> OnChainSignals:
    fg_val, fg_label = await fetch_fear_greed()
    funding = await fetch_funding_rates()

    btc_fr = funding.get("BTC", 0.0)
    eth_fr = funding.get("ETH", 0.0)

    score = 0.0
    details: dict[str, Any] = {
        "fear_greed": fg_val,
        "fear_greed_label": fg_label,
        "btc_funding": btc_fr,
        "eth_funding": eth_fr,
    }

    if fg_val < 20:
        score += 0.3
        details["fg_signal"] = "extreme_fear_contrarian_buy"
    elif fg_val < 35:
        score += 0.15
        details["fg_signal"] = "fear_lean_buy"
    elif fg_val > 80:
        score -= 0.3
        details["fg_signal"] = "extreme_greed_contrarian_sell"
    elif fg_val > 65:
        score -= 0.15
        details["fg_signal"] = "greed_lean_sell"

    if btc_fr > 0.0003:
        score -= 0.25
        details["funding_signal"] = "overleveraged_longs"
    elif btc_fr < -0.0003:
        score += 0.25
        details["funding_signal"] = "overleveraged_shorts"
    elif btc_fr > 0.0001:
        score -= 0.1
        details["funding_signal"] = "slightly_long_biased"
    elif btc_fr < -0.0001:
        score += 0.1
        details["funding_signal"] = "slightly_short_biased"

    score = max(-1.0, min(1.0, score))
    confidence = min(1.0, abs(score) * 1.4 + 0.1)
    signal = "buy" if score > 0.15 else ("sell" if score < -0.15 else "neutral")

    return OnChainSignals(
        fear_greed_index=fg_val,
        fear_greed_label=fg_label,
        btc_funding_rate=btc_fr,
        eth_funding_rate=eth_fr,
        signal=signal,
        score=round(score, 4),
        confidence=round(confidence, 4),
        details=details,
    )

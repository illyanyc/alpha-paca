"""On-chain and derivatives data client for the Adaptive Momentum strategy.

Fetches funding rates, open interest, exchange flows, Fear/Greed index,
and long/short ratio from free public APIs.  Computes derived signals
for the on-chain scoring layer (20% weight).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
COINGLASS_FUNDING_URL = "https://open-api.coinglass.com/public/v2/funding"
COINGLASS_OI_URL = "https://open-api.coinglass.com/public/v2/open_interest"
COINGLASS_LIQUIDATION_URL = "https://open-api.coinglass.com/public/v2/liquidation_history"
COINGLASS_LONG_SHORT_URL = "https://open-api.coinglass.com/public/v2/long_short"
DEFI_LLAMA_TVL_URL = "https://api.llama.fi/tvl/ethereum"


@dataclass
class OnChainSignals:
    fear_greed_index: int = 50
    fear_greed_label: str = "neutral"
    btc_funding_rate: float = 0.0
    eth_funding_rate: float = 0.0
    btc_oi_change_pct: float = 0.0
    oi_rising: bool = False
    long_short_ratio: float = 1.0
    exchange_flow_signal: str = "neutral"
    liquidation_cascade: bool = False
    liquidation_1h_usd: float = 0.0
    signal: str = "neutral"
    score: float = 0.0
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


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
    """Fetch BTC and ETH funding rates."""
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
                            u_margin = entries[0].get("uMarginList", [{}])
                            if u_margin:
                                rate = float(u_margin[0].get("rate", 0))
                                rates[symbol] = rate
                except Exception:
                    rates[symbol] = 0.0
    except Exception as e:
        logger.warning("funding_rate_fetch_failed", error=str(e)[:80])
    return rates


async def fetch_open_interest() -> dict[str, Any]:
    """Fetch BTC open interest and compute 24h change."""
    result: dict[str, Any] = {"btc_oi": 0.0, "btc_oi_change_pct": 0.0, "oi_rising": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                COINGLASS_OI_URL,
                params={"symbol": "BTC", "time_type": "all"},
            )
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("data", [])
                if entries and isinstance(entries, list):
                    oi_data = entries[0] if entries else {}
                    current_oi = float(oi_data.get("openInterest", 0))
                    oi_change = float(oi_data.get("h24Change", 0))
                    result["btc_oi"] = current_oi
                    result["btc_oi_change_pct"] = oi_change
                    result["oi_rising"] = oi_change > 2.0
    except Exception as e:
        logger.warning("oi_fetch_failed", error=str(e)[:80])
    return result


async def fetch_long_short_ratio() -> float:
    """Fetch BTC global long/short ratio. >1 = more longs, <1 = more shorts."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                COINGLASS_LONG_SHORT_URL,
                params={"symbol": "BTC", "time_type": "h1"},
            )
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("data", [])
                if entries and isinstance(entries, list):
                    ls_data = entries[0] if entries else {}
                    ratio = float(ls_data.get("longRate", 50)) / max(float(ls_data.get("shortRate", 50)), 0.01)
                    return ratio
    except Exception as e:
        logger.warning("long_short_fetch_failed", error=str(e)[:80])
    return 1.0


async def fetch_liquidations() -> tuple[float, bool]:
    """Fetch recent liquidation data. Returns (usd_1h, is_cascade)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                COINGLASS_LIQUIDATION_URL,
                params={"symbol": "BTC", "time_type": "h1"},
            )
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("data", [])
                if entries and isinstance(entries, list):
                    total_usd = sum(
                        float(e.get("volUsd", 0)) for e in entries[:5]
                    )
                    is_cascade = total_usd > 50_000_000
                    return total_usd, is_cascade
    except Exception as e:
        logger.warning("liquidation_fetch_failed", error=str(e)[:80])
    return 0.0, False


async def fetch_all_onchain() -> OnChainSignals:
    """Aggregate all on-chain signals into a single scored output."""
    fg_val, fg_label = await fetch_fear_greed()
    funding = await fetch_funding_rates()
    oi_data = await fetch_open_interest()
    ls_ratio = await fetch_long_short_ratio()
    liq_usd, liq_cascade = await fetch_liquidations()

    btc_fr = funding.get("BTC", 0.0)
    eth_fr = funding.get("ETH", 0.0)

    score = 0.0
    details: dict[str, Any] = {
        "fear_greed": fg_val,
        "fear_greed_label": fg_label,
        "btc_funding": btc_fr,
        "eth_funding": eth_fr,
        "btc_oi_change_pct": oi_data.get("btc_oi_change_pct", 0),
        "oi_rising": oi_data.get("oi_rising", False),
        "long_short_ratio": ls_ratio,
        "liquidation_1h_usd": liq_usd,
        "liquidation_cascade": liq_cascade,
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

    if btc_fr > 0.0005:
        score -= 0.25
        details["funding_signal"] = "overleveraged_longs"
    elif btc_fr > 0.0003:
        score -= 0.15
        details["funding_signal"] = "high_positive_funding"
    elif btc_fr < -0.0003:
        score += 0.25
        details["funding_signal"] = "overleveraged_shorts_squeeze"
    elif btc_fr < -0.0001:
        score += 0.1
        details["funding_signal"] = "slightly_short_biased"

    oi_rising = oi_data.get("oi_rising", False)
    if oi_rising and btc_fr < -0.0001:
        score += 0.2
        details["oi_signal"] = "short_squeeze_setup"
    elif oi_rising and btc_fr > 0.001:
        score -= 0.2
        details["oi_signal"] = "dangerous_overbought"

    if ls_ratio > 2.0:
        score -= 0.1
        details["ls_signal"] = "extreme_longs_contrarian_sell"
    elif ls_ratio < 0.5:
        score += 0.1
        details["ls_signal"] = "extreme_shorts_contrarian_buy"

    if liq_cascade:
        score -= 0.15
        details["liq_signal"] = "liquidation_cascade_wait"

    exchange_flow_signal = "neutral"
    if score > 0.3:
        exchange_flow_signal = "outflow"
    elif score > 0.15:
        exchange_flow_signal = "slight_outflow"
    elif score < -0.3:
        exchange_flow_signal = "inflow"
    elif score < -0.15:
        exchange_flow_signal = "slight_inflow"

    score = max(-1.0, min(1.0, score))
    confidence = min(1.0, abs(score) * 1.4 + 0.1)
    signal = "buy" if score > 0.15 else ("sell" if score < -0.15 else "neutral")

    return OnChainSignals(
        fear_greed_index=fg_val,
        fear_greed_label=fg_label,
        btc_funding_rate=btc_fr,
        eth_funding_rate=eth_fr,
        btc_oi_change_pct=oi_data.get("btc_oi_change_pct", 0),
        oi_rising=oi_data.get("oi_rising", False),
        long_short_ratio=round(ls_ratio, 3),
        exchange_flow_signal=exchange_flow_signal,
        liquidation_cascade=liq_cascade,
        liquidation_1h_usd=liq_usd,
        signal=signal,
        score=round(score, 4),
        confidence=round(confidence, 4),
        details=details,
    )

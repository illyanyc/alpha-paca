"""SwingSniper — patient macro trader bot for 1-30+ day crypto holds.

Evaluates 4H/daily candles, enters at structural support, exits at resistance.
Uses PydanticAI + Claude for decisions. Runs every ~1 hour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from agents.base import BaseAgent
from config import get_settings

logger = structlog.get_logger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "skills" / "crypto_swing_sniper.md"
LEARNINGS_KEY = "crypto:learnings:swing"


class SwingDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    pair: str
    conviction: float
    target_price: float
    stop_price: float
    hold_days_estimate: int
    reasoning: str


class SwingSniperOutput(BaseModel):
    decisions: list[SwingDecision]
    market_outlook: str
    summary: str


class SwingSniperAgent(BaseAgent):
    name = "swing_sniper"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._min_conviction = settings.crypto.min_conviction

        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=skill_text,
            output_type=SwingSniperOutput,
        )

    async def run(self, **kwargs) -> dict:
        r = await self._get_redis()

        indicators = kwargs.get("indicators", {})
        regime = kwargs.get("regime", {})
        news = kwargs.get("news", {})
        onchain = kwargs.get("onchain", {})
        positions = kwargs.get("positions", [])
        portfolio = kwargs.get("portfolio", {})
        candles_4h = kwargs.get("candles_4h", {})
        prices = kwargs.get("prices", {})

        learnings_raw = await r.get(LEARNINGS_KEY)
        learnings = json.loads(learnings_raw) if learnings_raw else []

        self.think("Analyzing 4H/daily structure for swing setups...")

        prompt = self._build_prompt(
            indicators=indicators,
            regime=regime,
            news=news,
            onchain=onchain,
            positions=positions,
            portfolio=portfolio,
            candles_4h=candles_4h,
            prices=prices,
            learnings=learnings,
        )

        result = await self._agent.run(prompt)
        output = result.output

        self.think(f"Swing outlook: {output.market_outlook}")

        actionable = [
            d for d in output.decisions
            if d.action != "HOLD" and d.conviction >= self._min_conviction
        ]

        for d in output.decisions:
            tag = ">>>" if d in actionable else "---"
            self.think(f"{tag} {d.pair}: {d.action} conv={d.conviction:.2f} target=${d.target_price:,.0f} stop=${d.stop_price:,.0f} — {d.reasoning[:80]}")

        if actionable:
            self.think(f"Swing: {len(actionable)} actionable trade(s)")
        else:
            self.think(f"Swing: HOLD on all {len(output.decisions)} pairs")

        return {
            "decisions": [d.model_dump() for d in actionable],
            "all_decisions": [d.model_dump() for d in output.decisions],
            "market_outlook": output.market_outlook,
            "summary": output.summary,
        }

    def _build_prompt(
        self,
        indicators: dict,
        regime: dict,
        news: dict,
        onchain: dict,
        positions: list,
        portfolio: dict,
        candles_4h: dict,
        prices: dict,
        learnings: list,
    ) -> str:
        settings = get_settings()
        sections: list[str] = []

        sections.append(f"## Tracked Pairs: {', '.join(settings.crypto.pair_list)}")

        nav = portfolio.get("nav", 0)
        cap = settings.crypto.max_capital
        cap_label = f"${nav:,.2f} (whole account)" if cap <= 0 else f"${cap:,.0f}"
        sections.append(f"## Capital: {cap_label}")

        if portfolio:
            sections.append(
                f"## Portfolio\nNAV: ${nav:,.2f} | Cash: ${portfolio.get('cash', 0):,.2f} | "
                f"Exposure: {portfolio.get('total_exposure_pct', 0):.1f}% | "
                f"Drawdown: {portfolio.get('drawdown_pct', 0):.1f}% | "
                f"Daily PnL: ${portfolio.get('realized_pnl_today', 0):+,.2f}"
            )

        if positions:
            pos_lines = ["## Current Swing Positions"]
            for p in positions:
                if p.get("bot_id") != "swing":
                    continue
                pair = p.get("pair", "?")
                entry = float(p.get("avg_entry_price", 0))
                current = float(p.get("current_price", 0))
                pnl_pct = float(p.get("unrealized_pnl_pct", 0))
                mv = float(p.get("market_value_usd", 0))
                pos_lines.append(
                    f"- {pair}: entry=${entry:,.2f}, now=${current:,.2f}, "
                    f"PnL={pnl_pct:+.1f}%, value=${mv:,.2f}"
                )
            if len(pos_lines) > 1:
                sections.append("\n".join(pos_lines))
            else:
                sections.append("## Current Swing Positions\nNONE — all cash for swing bot.")
        else:
            sections.append("## Current Swing Positions\nNONE — all cash for swing bot.")

        if regime:
            r_label = regime.get("label", "UNKNOWN")
            r_conf = regime.get("confidence", 0)
            features = regime.get("features", {})
            sections.append(
                f"## Market Regime: [{r_label}] (conf={r_conf:.0%})\n"
                f"Vol={features.get('realized_vol', 0):.2f}, "
                f"Hurst={features.get('hurst_exponent', 0.5):.3f}, "
                f"Trend={features.get('trend_strength', 0):.3f}"
            )

        for pair in settings.crypto.pair_list:
            pair_ind = indicators.get(pair, {})
            if pair_ind:
                sections.append(
                    f"## {pair} — 4H Indicators\n"
                    f"RSI={pair_ind.get('rsi', 'N/A')}, "
                    f"MACD={pair_ind.get('macd', 'N/A')}, "
                    f"BB_upper={pair_ind.get('bb_upper', 'N/A')}, "
                    f"BB_lower={pair_ind.get('bb_lower', 'N/A')}, "
                    f"VWAP={pair_ind.get('vwap', 'N/A')}, "
                    f"ATR={pair_ind.get('atr', 'N/A')}, "
                    f"EMA_20={pair_ind.get('ema_20', 'N/A')}, "
                    f"EMA_50={pair_ind.get('ema_50', 'N/A')}"
                )

            pair_candles = candles_4h.get(pair, [])
            if pair_candles:
                recent = pair_candles[-10:]
                candle_lines = [f"## {pair} — Recent 4H Candles (last {len(recent)})"]
                for c in recent:
                    candle_lines.append(
                        f"  O={c.get('open', 0):,.2f} H={c.get('high', 0):,.2f} "
                        f"L={c.get('low', 0):,.2f} C={c.get('close', 0):,.2f} "
                        f"V={c.get('volume', 0):,.0f}"
                    )
                sections.append("\n".join(candle_lines))

            pair_price = prices.get(pair, {})
            if pair_price:
                sections.append(
                    f"## {pair} — Current Price\n"
                    f"Mid=${pair_price.get('mid', 0):,.2f}, "
                    f"Bid=${pair_price.get('bid', 0):,.2f}, "
                    f"Ask=${pair_price.get('ask', 0):,.2f}"
                )

        if onchain:
            sections.append(
                f"## On-Chain\nFear/Greed: {onchain.get('fear_greed_index', 50)} "
                f"({onchain.get('fear_greed_label', '?')}), "
                f"BTC Funding: {onchain.get('btc_funding_rate', 0):.4%}"
            )

        if news and isinstance(news, dict):
            news_lines = [
                f"## News Sentiment: {news.get('overall_sentiment', 'N/A')} "
                f"(score={news.get('overall_score', 0):.2f})"
            ]
            for evt in news.get("key_events", [])[:5]:
                news_lines.append(f"- {evt}")
            sections.append("\n".join(news_lines))

        if learnings:
            learning_lines = ["## Recent Learnings (auto-generated from backtest analysis)"]
            for l_item in learnings[:15]:
                learning_lines.append(f"- {l_item}")
            sections.append("\n".join(learning_lines))

        sections.append(
            f"\nProvide BUY/SELL/HOLD for each pair. "
            f"Min conviction to trade: {self._min_conviction}. "
            f"Min R/R ratio: {settings.crypto.swing_min_rr_ratio}:1. "
            f"HOLD is the default — only trade high-conviction structural setups."
        )

        return "\n\n".join(sections)

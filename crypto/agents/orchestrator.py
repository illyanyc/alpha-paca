"""OrchestratorAgent — combines all signals, decides entry/exit via PydanticAI + Claude."""

from __future__ import annotations

import json
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from agents.base import BaseAgent
from config import get_settings

logger = structlog.get_logger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "skills" / "crypto_orchestrator.md"
TECH_SIGNAL_KEY = "crypto:signals:technical"
FUND_SIGNAL_KEY = "crypto:signals:fundamental"
NEWS_CACHE_KEY = "crypto:news:sentiment"


class TradeDecision(BaseModel):
    action: str  # BUY / SELL / HOLD
    pair: str
    size_pct: float  # % of capital to allocate
    confidence: float  # 0.0 to 1.0
    reasoning: str
    urgency: str  # immediate / normal / low


class OrchestratorOutput(BaseModel):
    decisions: list[TradeDecision]
    market_outlook: str  # bullish / bearish / neutral / mixed
    summary: str


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=(
                "You are the head crypto portfolio manager. You receive technical, "
                "fundamental, and news signals for multiple crypto pairs. Your job is "
                "to make BUY/SELL/HOLD decisions with position sizing.\n\n"
                "RULES:\n"
                "- Only output BUY/SELL actions for confidence >= 0.7\n"
                "- Alpaca crypto is LONG-ONLY: SELL means exit position to cash\n"
                "- Never exceed max position or exposure limits\n"
                "- Provide clear reasoning for every decision\n"
                "- Consider correlation between assets\n\n"
                f"{skill_text}"
            ),
            output_type=OrchestratorOutput,
        )
        self._confidence_threshold = settings.crypto.confidence_threshold

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        r = await self._get_redis()

        tech_raw = await r.get(TECH_SIGNAL_KEY)
        fund_raw = await r.get(FUND_SIGNAL_KEY)
        news_raw = await r.get(NEWS_CACHE_KEY)

        tech_signals = json.loads(tech_raw) if tech_raw else {}
        fund_signals = json.loads(fund_raw) if fund_raw else {}
        news_data = json.loads(news_raw) if news_raw else {}

        positions_raw = kwargs.get("positions", [])
        portfolio_state = kwargs.get("portfolio_state", {})

        prompt = self._build_prompt(
            tech_signals, fund_signals, news_data, positions_raw, portfolio_state, settings
        )

        result = await self._agent.run(prompt)
        output = result.output

        actionable = [
            d for d in output.decisions
            if d.action != "HOLD" and d.confidence >= self._confidence_threshold
        ]

        logger.info(
            "orchestrator_decision",
            total_decisions=len(output.decisions),
            actionable=len(actionable),
            outlook=output.market_outlook,
        )

        return {
            "decisions": [d.model_dump() for d in actionable],
            "all_decisions": [d.model_dump() for d in output.decisions],
            "market_outlook": output.market_outlook,
            "summary": output.summary,
        }

    def _build_prompt(
        self,
        tech: dict,
        fund: dict,
        news: dict,
        positions: list,
        portfolio: dict,
        settings,
    ) -> str:
        sections = [
            f"## Tracked Pairs: {', '.join(settings.crypto.pair_list)}",
            f"## Capital: ${settings.crypto.max_capital:,.0f}",
            f"## Max Position: {settings.crypto.max_position_pct}% | Max Exposure: {settings.crypto.max_total_exposure_pct}%",
        ]

        if portfolio:
            sections.append(
                f"## Portfolio State\n"
                f"NAV: ${portfolio.get('nav', 0):,.2f} | Cash: ${portfolio.get('cash', 0):,.2f} | "
                f"Exposure: {portfolio.get('total_exposure_pct', 0):.1f}% | "
                f"Drawdown: {portfolio.get('drawdown_pct', 0):.1f}%"
            )

        if positions:
            pos_lines = ["## Current Positions"]
            for p in positions:
                pos_lines.append(
                    f"- {p.get('pair', p.get('symbol', '?'))}: "
                    f"qty={p.get('qty', 0)}, entry=${p.get('avg_entry_price', 0):,.2f}, "
                    f"current=${p.get('current_price', 0):,.2f}, "
                    f"pnl=${p.get('unrealized_pnl', p.get('unrealized_pl', 0)):+,.2f}"
                )
            sections.append("\n".join(pos_lines))

        if tech:
            tech_lines = ["## Technical Signals"]
            for pair, data in tech.items():
                tech_lines.append(
                    f"- {pair}: {data.get('signal', 'N/A')} (score={data.get('score', 0):.2f}, "
                    f"conf={data.get('confidence', 0):.2f}) — {data.get('details', '')}"
                )
            sections.append("\n".join(tech_lines))

        if fund:
            fund_lines = ["## Fundamental Signals"]
            for pair, data in fund.items():
                fund_lines.append(
                    f"- {pair}: {data.get('signal', 'N/A')} (score={data.get('score', 0):.2f}) — "
                    f"{data.get('details', '')}"
                )
            sections.append("\n".join(fund_lines))

        if news:
            news_lines = ["## News Sentiment"]
            if isinstance(news, dict):
                news_lines.append(
                    f"Overall: {news.get('overall_sentiment', 'N/A')} "
                    f"(score={news.get('overall_score', 0):.2f})"
                )
                for evt in news.get("key_events", [])[:5]:
                    news_lines.append(f"- {evt}")
            sections.append("\n".join(news_lines))

        sections.append(
            "\nMake BUY/SELL/HOLD decisions for each pair. "
            "Only recommend actions with confidence >= 0.7. "
            "For SELL, this means close the position to go to cash."
        )

        return "\n\n".join(sections)

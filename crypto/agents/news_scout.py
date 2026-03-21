"""NewsScoutAgent — scans crypto news via Serper/Tavily, classifies sentiment with PydanticAI."""

from __future__ import annotations

import json
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from agents.base import BaseAgent
from config import get_settings
from services.news_client import NewsClient

logger = structlog.get_logger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "skills" / "crypto_news_analysis.md"
NEWS_CACHE_KEY = "crypto:news:sentiment"
NEWS_CACHE_TTL = 3600


class ArticleSentiment(BaseModel):
    title: str
    sentiment: str  # bullish / bearish / neutral
    urgency: str  # high / medium / low
    affected_coins: list[str]
    summary: str


class NewsAnalysisResult(BaseModel):
    articles: list[ArticleSentiment]
    overall_sentiment: str  # bullish / bearish / neutral
    overall_score: float  # -1.0 to 1.0
    key_events: list[str]


class NewsScoutAgent(BaseAgent):
    name = "news_scout"

    def __init__(self) -> None:
        super().__init__()
        self._news_client = NewsClient()
        settings = get_settings()

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=(
                "You are a crypto news analyst. Analyze each news article and classify "
                "sentiment, urgency, and affected coins. Return structured JSON.\n\n"
                f"{skill_text}"
            ),
            output_type=NewsAnalysisResult,
        )

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        pairs = settings.crypto.pair_list

        raw_news = await self._news_client.fetch_crypto_news(pairs)

        all_articles = []
        for query, articles in raw_news.items():
            for a in articles:
                all_articles.append(f"[{a.get('title', '')}] {a.get('snippet', a.get('content', ''))[:300]}")

        if not all_articles:
            logger.info("no_news_found")
            return {"articles": [], "overall_sentiment": "neutral", "overall_score": 0.0}

        prompt = (
            f"Analyze these {len(all_articles)} crypto news articles. "
            f"Tracked pairs: {', '.join(pairs)}.\n\n"
            + "\n---\n".join(all_articles[:20])
        )

        result = await self._agent.run(prompt)
        analysis = result.output

        r = await self._get_redis()
        await r.set(NEWS_CACHE_KEY, analysis.model_dump_json(), ex=NEWS_CACHE_TTL)

        logger.info(
            "news_analysis_complete",
            article_count=len(analysis.articles),
            overall=analysis.overall_sentiment,
            score=analysis.overall_score,
        )

        return analysis.model_dump()

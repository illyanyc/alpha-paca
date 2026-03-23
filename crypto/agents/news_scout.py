"""NewsScoutAgent — fast crypto news scanner with LLM sentiment classification.

Optimized for the Adaptive Momentum strategy:
  - Polls Serper/Tavily every 10 seconds (configurable)
  - Uses claude-3-5-haiku for fast classification (<200ms)
  - Caches sentiment in Redis with 60s TTL for momentum_trader re-use
  - Urgency fast-path: hack/exploit/listing news triggers immediate flag
"""

from __future__ import annotations

import json
import time
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
NEWS_CACHE_TTL = 60

URGENT_KEYWORDS_BEARISH = [
    "hack", "exploit", "rug pull", "security breach", "stolen",
    "ban", "crackdown", "lawsuit", "sec charges",
]
URGENT_KEYWORDS_BULLISH = [
    "coinbase listing", "etf approved", "etf approval",
    "partnership", "institutional adoption",
]


class ArticleSentiment(BaseModel):
    title: str
    sentiment: str
    urgency: str
    affected_coins: list[str]
    summary: str


class NewsAnalysisResult(BaseModel):
    articles: list[ArticleSentiment]
    overall_sentiment: str
    overall_score: float
    key_events: list[str]


class NewsScoutAgent(BaseAgent):
    name = "news_scout"

    def __init__(self) -> None:
        super().__init__()
        self._news_client = NewsClient()
        self._last_poll_time: float = 0
        self._cached_result: dict | None = None

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._agent = Agent(
            "anthropic:claude-3-5-haiku-latest",
            instructions=(
                "You are a crypto news analyst. Analyze each article, classify "
                "sentiment (bullish/bearish/neutral), urgency (high/medium/low), "
                "and affected coins. Be concise.\n\n"
                f"{skill_text}"
            ),
            output_type=NewsAnalysisResult,
        )

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        pairs = settings.crypto.pair_list
        poll_interval = settings.crypto.news_poll_interval_sec

        now = time.monotonic()
        if self._cached_result and (now - self._last_poll_time) < poll_interval:
            return self._cached_result

        self.think(f"Scanning news for {len(pairs)} pairs...")
        raw_news = await self._news_client.fetch_crypto_news(pairs)

        all_articles: list[str] = []
        all_titles: list[str] = []
        for _query, articles in raw_news.items():
            for a in articles:
                title = a.get("title", "")
                snippet = a.get("snippet", a.get("content", ""))[:300]
                all_articles.append(f"[{title}] {snippet}")
                all_titles.append(title.lower())

        if not all_articles:
            self.think("No news articles found")
            result = {"articles": [], "overall_sentiment": "neutral", "overall_score": 0.0, "key_events": []}
            self._cached_result = result
            self._last_poll_time = now
            return result

        urgent = self._check_urgent_keywords(all_titles)
        if urgent:
            self.think(f"URGENT news detected: {urgent['type']} — {urgent['keyword']}")

        self.think(f"Found {len(all_articles)} articles, classifying with haiku...")

        prompt = (
            f"Analyze these {len(all_articles)} crypto news articles. "
            f"Tracked pairs: {', '.join(pairs)}.\n\n"
            + "\n---\n".join(all_articles[:15])
        )

        try:
            result = await self._agent.run(prompt)
            analysis = result.output
        except Exception as e:
            logger.warning("news_llm_failed", error=str(e)[:100])
            fallback = {
                "articles": [],
                "overall_sentiment": "neutral",
                "overall_score": 0.0,
                "key_events": [],
            }
            if urgent:
                fallback["overall_score"] = urgent.get("score_adjustment", 0.0)
                fallback["overall_sentiment"] = "bearish" if urgent["type"] == "bearish" else "bullish"
                fallback["key_events"] = [f"URGENT: {urgent['keyword']}"]
            self._cached_result = fallback
            self._last_poll_time = now
            return fallback

        output = analysis.model_dump()

        if urgent:
            adj = urgent.get("score_adjustment", 0.0)
            output["overall_score"] = max(-1.0, min(1.0, output["overall_score"] + adj))
            output["key_events"].insert(0, f"URGENT: {urgent['keyword']}")

        r = await self._get_redis()
        await r.set(NEWS_CACHE_KEY, json.dumps(output), ex=NEWS_CACHE_TTL)

        self.think(
            f"News: {output['overall_sentiment']} (score={output['overall_score']:.2f}), "
            f"{len(output.get('key_events', []))} key events"
        )

        self._cached_result = output
        self._last_poll_time = now
        return output

    @staticmethod
    def _check_urgent_keywords(titles: list[str]) -> dict | None:
        """Fast keyword scan for high-urgency events."""
        combined = " ".join(titles)
        for kw in URGENT_KEYWORDS_BEARISH:
            if kw in combined:
                return {"type": "bearish", "keyword": kw, "score_adjustment": -0.5}
        for kw in URGENT_KEYWORDS_BULLISH:
            if kw in combined:
                return {"type": "bullish", "keyword": kw, "score_adjustment": 0.4}
        return None

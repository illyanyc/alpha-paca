"""Crypto news aggregation via Serper and Tavily APIs."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

SERPER_URL = "https://google.serper.dev/news"
TAVILY_URL = "https://api.tavily.com/search"


class NewsClient:
    """Fetches crypto news from Serper (headlines) and Tavily (full articles)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._serper_key = settings.api_keys.serper_api_key
        self._tavily_key = settings.api_keys.tavily_api_key

    async def search_serper(self, query: str, num: int = 10) -> list[dict]:
        """Search Serper News API for crypto headlines."""
        if not self._serper_key:
            logger.warning("serper_disabled")
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    SERPER_URL,
                    json={"q": query, "num": num},
                    headers={"X-API-KEY": self._serper_key},
                )
                resp.raise_for_status()
                data = resp.json()
            articles = []
            for item in data.get("news", []):
                articles.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                    "source": item.get("source", ""),
                    "date": item.get("date", ""),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
            logger.info("serper_results", query=query, count=len(articles))
            return articles
        except Exception:
            logger.exception("serper_search_failed", query=query)
            return []

    async def search_tavily(self, query: str, max_results: int = 5) -> list[dict]:
        """Extract full article content via Tavily search."""
        if not self._tavily_key:
            logger.warning("tavily_disabled")
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    TAVILY_URL,
                    json={
                        "api_key": self._tavily_key,
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": max_results,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            articles = []
            for r in data.get("results", []):
                articles.append({
                    "title": r.get("title", ""),
                    "content": r.get("content", ""),
                    "url": r.get("url", ""),
                    "score": r.get("score", 0),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
            logger.info("tavily_results", query=query, count=len(articles))
            return articles
        except Exception:
            logger.exception("tavily_search_failed", query=query)
            return []

    async def fetch_crypto_news(self, pairs: list[str]) -> dict[str, list[dict]]:
        """Aggregate news for each crypto pair + general market."""
        all_news: dict[str, list[dict]] = {}

        queries = ["crypto market news today", "cryptocurrency regulation news"]
        for pair in pairs:
            coin = pair.split("/")[0]
            queries.append(f"{coin} cryptocurrency news")

        for query in queries:
            serper_results = await self.search_serper(query, num=5)
            tavily_results = await self.search_tavily(query, max_results=3)
            all_news[query] = serper_results + tavily_results

        return all_news

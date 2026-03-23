"""Crypto news aggregation via Serper and Tavily APIs."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

SERPER_URL = "https://google.serper.dev/news"
TAVILY_URL = "https://api.tavily.com/search"

_CIRCUIT_COOLDOWN = 600  # 10 min backoff after repeated failures


class _ApiCircuit:
    """Simple circuit breaker: after `threshold` failures, stop trying for `cooldown` seconds."""

    def __init__(self, name: str, threshold: int = 2, cooldown: int = _CIRCUIT_COOLDOWN):
        self.name = name
        self._threshold = threshold
        self._cooldown = cooldown
        self._fail_count = 0
        self._open_until: float = 0
        self._last_log: float = 0

    @property
    def is_open(self) -> bool:
        if time.monotonic() >= self._open_until:
            if self._fail_count >= self._threshold:
                self._fail_count = 0
            return False
        return True

    def record_success(self) -> None:
        self._fail_count = 0
        self._open_until = 0

    def record_failure(self, error: str) -> None:
        self._fail_count += 1
        now = time.monotonic()
        if self._fail_count >= self._threshold:
            self._open_until = now + self._cooldown
            if now - self._last_log > 60:
                logger.warning(
                    f"{self.name}_circuit_open",
                    failures=self._fail_count,
                    retry_in_sec=self._cooldown,
                    error=error[:120],
                )
                self._last_log = now
        elif now - self._last_log > 30:
            logger.error(f"{self.name}_failed", error=error[:120])
            self._last_log = now


class NewsClient:
    """Fetches crypto news from Serper (headlines) and Tavily (full articles)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._serper_key = settings.api_keys.serper_api_key
        self._tavily_key = settings.api_keys.tavily_api_key
        self._serper_circuit = _ApiCircuit("serper")
        self._tavily_circuit = _ApiCircuit("tavily")

    async def search_serper(self, query: str, num: int = 10) -> list[dict]:
        """Search Serper News API for crypto headlines."""
        if not self._serper_key or self._serper_circuit.is_open:
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
            self._serper_circuit.record_success()
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
            return articles
        except Exception as exc:
            self._serper_circuit.record_failure(str(exc))
            return []

    async def search_tavily(self, query: str, max_results: int = 5) -> list[dict]:
        """Extract full article content via Tavily search."""
        if not self._tavily_key or self._tavily_circuit.is_open:
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
            self._tavily_circuit.record_success()
            articles = []
            for r in data.get("results", []):
                articles.append({
                    "title": r.get("title", ""),
                    "content": r.get("content", ""),
                    "url": r.get("url", ""),
                    "score": r.get("score", 0),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
            return articles
        except Exception as exc:
            self._tavily_circuit.record_failure(str(exc))
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

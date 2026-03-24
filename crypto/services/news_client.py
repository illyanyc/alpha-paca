"""Crypto news aggregation via Serper headlines + trafilatura full-text scraping.

Replaces Tavily ($0.05-0.10/query) with Serper ($0.001/query) + trafilatura ($0).
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import structlog
import trafilatura

from config import get_settings

logger = structlog.get_logger(__name__)

SERPER_URL = "https://google.serper.dev/news"
_CIRCUIT_COOLDOWN = 600
_SCRAPE_POOL = ThreadPoolExecutor(max_workers=4)
_SCRAPE_TIMEOUT = 10


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


def _scrape_article(url: str) -> str | None:
    """Synchronous full-text extraction via trafilatura."""
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return None
        return trafilatura.extract(html, include_comments=False)
    except Exception:
        return None


async def _scrape_article_async(url: str) -> str | None:
    """Run trafilatura in a thread pool to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_SCRAPE_POOL, _scrape_article, url),
            timeout=_SCRAPE_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        return None


class NewsClient:
    """Fetches crypto news from Serper and scrapes full article text with trafilatura."""

    def __init__(self) -> None:
        settings = get_settings()
        self._serper_key = settings.api_keys.serper_api_key
        self._serper_circuit = _ApiCircuit("serper")
        self._scrape_cache: dict[str, list[dict]] = {}
        self._scrape_last_fetch: float = 0
        self._scrape_interval: float = float(settings.crypto.article_scrape_interval_sec)

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

    async def _scrape_top_articles(self, articles: list[dict], max_scrape: int = 3) -> list[dict]:
        """Scrape full text for the top N articles using trafilatura."""
        enriched: list[dict] = []
        tasks = []
        for article in articles[:max_scrape]:
            url = article.get("link", "")
            if url:
                tasks.append((article, _scrape_article_async(url)))

        for article, coro in tasks:
            content = await coro
            entry = dict(article)
            if content:
                entry["content"] = content[:2000]
            enriched.append(entry)

        for article in articles[max_scrape:]:
            enriched.append(article)

        return enriched

    async def fetch_crypto_news(self, pairs: list[str]) -> dict[str, list[dict]]:
        """Aggregate news for each crypto pair + general market.

        Serper headlines are fetched every poll cycle.
        Full-text scraping via trafilatura runs at most once per
        article_scrape_interval_sec (default 1h) and results are cached.
        """
        all_news: dict[str, list[dict]] = {}

        queries = ["crypto market news today", "cryptocurrency regulation news"]
        for pair in pairs:
            coin = pair.split("/")[0]
            queries.append(f"{coin} cryptocurrency news")

        now = time.monotonic()
        scrape_fresh = (now - self._scrape_last_fetch) >= self._scrape_interval

        if scrape_fresh:
            self._scrape_cache.clear()

        for query in queries:
            serper_results = await self.search_serper(query, num=5)

            if scrape_fresh and serper_results:
                enriched = await self._scrape_top_articles(serper_results, max_scrape=3)
                self._scrape_cache[query] = enriched
                all_news[query] = enriched
            elif not scrape_fresh and query in self._scrape_cache:
                all_news[query] = serper_results + [
                    a for a in self._scrape_cache[query]
                    if a.get("content") and a not in serper_results
                ]
            else:
                all_news[query] = serper_results

        if scrape_fresh:
            self._scrape_last_fetch = now
            total_scraped = sum(
                1 for arts in self._scrape_cache.values()
                for a in arts if a.get("content")
            )
            logger.info(
                "articles_scraped",
                queries=len(queries),
                articles_with_fulltext=total_scraped,
                next_scrape_in_sec=int(self._scrape_interval),
            )

        return all_news

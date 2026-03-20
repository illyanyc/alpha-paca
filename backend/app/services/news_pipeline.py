"""Aggregate news from Serper (Google News) and Tavily."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def _normalize_item(raw: dict[str, Any], source: str) -> dict[str, Any]:
    url = raw.get("url") or raw.get("link") or ""
    title = raw.get("title") or raw.get("headline") or ""
    snippet = raw.get("snippet") or raw.get("content") or ""
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "source": raw.get("source") or source,
        "provider": source,
    }


class NewsPipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def search_serper(self, query: str) -> list[dict[str, Any]]:
        key = self._settings.api_keys.serper_api_key
        if not key:
            logger.warning("serper_api_key_missing")
            return []
        url = "https://google.serper.dev/news"
        headers = {"X-API-KEY": key, "Content-Type": "application/json"}
        payload = {"q": query, "num": 20}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        news = data.get("news") or []
        return [_normalize_item(n, "serper") for n in news]

    async def search_tavily(self, query: str) -> list[dict[str, Any]]:
        key = self._settings.api_keys.tavily_api_key
        if not key:
            logger.warning("tavily_api_key_missing")
            return []
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": key,
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "max_results": 15,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or []
        return [_normalize_item(r, "tavily") for r in results]

    async def aggregate_news(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Search per symbol via Serper + Tavily; deduplicate by canonical URL."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for sym in symbols:
            q = f"{sym} stock news"
            serper_items = await self.search_serper(q)
            tavily_items = await self.search_tavily(q)
            for item in serper_items + tavily_items:
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                key = urlparse(url)._replace(fragment="").geturl()
                if key in seen:
                    continue
                seen.add(key)
                item["symbol"] = sym.upper()
                out.append(item)
        return out

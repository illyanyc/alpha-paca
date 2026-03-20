"""Minimal SEC EDGAR API client (company submissions feed)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

SEC_DATA_BASE = "https://data.sec.gov"
DEFAULT_USER_AGENT = (
    "AlphaPaca/0.1 (contact: support@example.com) python-httpx"  # customize for production
)


async def get_recent_filings(cik: str, *, user_agent: str | None = None) -> dict[str, Any]:
    """Fetch recent filing metadata for a CIK from the SEC submissions JSON endpoint.

    CIK should be zero-padded to 10 digits when calling the SEC API.
    """
    cik_norm = cik.zfill(10)
    url = f"{SEC_DATA_BASE}/submissions/CIK{cik_norm}.json"
    headers = {"User-Agent": user_agent or DEFAULT_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("sec_edgar_submissions", cik=cik_norm, keys=list(data.keys())[:10])
        return data

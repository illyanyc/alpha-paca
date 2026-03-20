"""CRUD helpers for the `watchlist` table."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Watchlist

logger = structlog.get_logger(__name__)


class WatchlistManager:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_symbol(
        self,
        symbol: str,
        pod_name: str,
        priority: float,
        reason: str,
    ) -> Watchlist:
        row = Watchlist(
            symbol=symbol.upper(),
            pod_name=pod_name,
            priority_score=priority,
            reason=reason,
            removed_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("watchlist_add", symbol=row.symbol, pod=pod_name)
        return row

    async def remove_symbol(self, symbol: str, pod_name: str) -> int:
        stmt = (
            update(Watchlist)
            .where(
                Watchlist.symbol == symbol.upper(),
                Watchlist.pod_name == pod_name,
                Watchlist.removed_at.is_(None),
            )
            .values(removed_at=datetime.now(timezone.utc))
        )
        res = await self._session.execute(stmt)
        return res.rowcount or 0

    async def get_active_watchlist(self, pod_name: str | None = None) -> list[Watchlist]:
        stmt = select(Watchlist).where(Watchlist.removed_at.is_(None))
        if pod_name is not None:
            stmt = stmt.where(Watchlist.pod_name == pod_name)
        stmt = stmt.order_by(Watchlist.priority_score.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

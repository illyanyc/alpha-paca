"""Closed and open trades with aggregate statistics."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Trade

router = APIRouter()


@router.get("/stats")
async def trade_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    pod_name: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    def _filters(q):
        if pod_name is not None:
            q = q.where(Trade.pod_name == pod_name)
        if symbol is not None:
            q = q.where(Trade.symbol == symbol)
        if status is not None:
            q = q.where(Trade.status == status)
        return q

    total_stmt = _filters(select(func.count(Trade.id)))
    total = int((await session.execute(total_stmt)).scalar_one() or 0)

    agg_stmt = _filters(
        select(
            func.count(Trade.id),
            func.coalesce(func.avg(Trade.pnl), 0.0),
            func.coalesce(
                func.sum(case((Trade.pnl > 0, 1), else_=0)),
                0,
            ),
        ).where(Trade.exit_time.isnot(None), Trade.pnl.isnot(None))
    )
    row = (await session.execute(agg_stmt)).one()
    closed_count = int(row[0] or 0)
    avg_pnl = float(row[1] or 0.0)
    wins = int(row[2] or 0)
    win_rate = (wins / closed_count) if closed_count else None

    return {
        "total_trades": total,
        "closed_trades": closed_count,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "wins": wins,
    }


@router.get("/")
async def list_trades(
    session: Annotated[AsyncSession, Depends(get_session)],
    pod_name: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=10_000),
) -> list[dict]:
    stmt = select(Trade)
    if pod_name is not None:
        stmt = stmt.where(Trade.pod_name == pod_name)
    if symbol is not None:
        stmt = stmt.where(Trade.symbol == symbol)
    if status is not None:
        stmt = stmt.where(Trade.status == status)
    stmt = stmt.order_by(desc(Trade.entry_time)).limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]

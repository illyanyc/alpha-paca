"""Portfolio snapshot and history."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import PortfolioState

router = APIRouter()


@router.get("/state")
async def get_portfolio_state(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(
        select(PortfolioState).order_by(desc(PortfolioState.updated_at)).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No portfolio state recorded yet",
        )
    return jsonable_encoder(row)


@router.get("/history")
async def get_portfolio_history(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int | None, Query(ge=1, le=10_000)] = None,
) -> list[dict]:
    stmt = select(PortfolioState).order_by(desc(PortfolioState.updated_at))
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]

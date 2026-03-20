"""Backtest qualification results."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import BacktestResult

router = APIRouter()


@router.get("/results")
async def list_backtest_results(
    session: Annotated[AsyncSession, Depends(get_session)],
    pod_name: str | None = None,
) -> list[dict]:
    stmt = select(BacktestResult).order_by(desc(BacktestResult.calculated_at))
    if pod_name is not None:
        stmt = stmt.where(BacktestResult.pod_name == pod_name)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/results/{result_id}")
async def get_backtest_result(
    result_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(
        select(BacktestResult).where(BacktestResult.id == result_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backtest result not found",
        )
    return jsonable_encoder(row)

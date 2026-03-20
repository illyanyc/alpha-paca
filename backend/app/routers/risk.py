"""Risk metrics — VaR, factors, stress tests, events, drawdown."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import (
    DrawdownState,
    FactorExposure,
    RiskEvent,
    StressTestResult,
    VarHistory,
)

router = APIRouter()


@router.get("/var")
async def get_latest_var(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(
        select(VarHistory).order_by(desc(VarHistory.calculated_at)).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No VaR history available",
        )
    return jsonable_encoder(row)


@router.get("/factors")
async def get_latest_factors(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(
        select(FactorExposure).order_by(desc(FactorExposure.snapshot_time)).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No factor exposure snapshot available",
        )
    return jsonable_encoder(row)


@router.get("/stress-tests")
async def list_stress_tests(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    result = await session.execute(
        select(StressTestResult).order_by(desc(StressTestResult.calculated_at))
    )
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/events")
async def list_risk_events(
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    stmt = select(RiskEvent)
    if severity is not None:
        stmt = stmt.where(RiskEvent.severity == severity)
    stmt = stmt.order_by(desc(RiskEvent.created_at)).limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/drawdown")
async def get_drawdown_state(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(
        select(DrawdownState).order_by(desc(DrawdownState.updated_at)).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No drawdown state recorded",
        )
    return jsonable_encoder(row)

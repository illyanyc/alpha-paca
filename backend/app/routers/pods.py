"""Strategy pods — allocations, signals, and performance."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import PodAllocation, PodPerformance, PodSignal

router = APIRouter()


class PodDetailOut(BaseModel):
    allocation: dict
    signals: list[dict]
    performance: list[dict]


@router.get("/")
async def list_pods(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    result = await session.execute(
        select(PodAllocation).order_by(PodAllocation.pod_name.asc())
    )
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/{pod_name}", response_model=PodDetailOut)
async def get_pod(
    pod_name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PodDetailOut:
    alloc_res = await session.execute(
        select(PodAllocation).where(PodAllocation.pod_name == pod_name)
    )
    allocation = alloc_res.scalar_one_or_none()
    if allocation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pod allocation not found: {pod_name}",
        )

    sig_res = await session.execute(
        select(PodSignal)
        .where(PodSignal.pod_name == pod_name)
        .order_by(desc(PodSignal.created_at))
        .limit(50)
    )
    signals = [jsonable_encoder(s) for s in sig_res.scalars().all()]

    perf_res = await session.execute(
        select(PodPerformance)
        .where(PodPerformance.pod_name == pod_name)
        .order_by(desc(PodPerformance.period_end))
        .limit(20)
    )
    performance = [jsonable_encoder(p) for p in perf_res.scalars().all()]

    return PodDetailOut(
        allocation=jsonable_encoder(allocation),
        signals=signals,
        performance=performance,
    )

"""Pod signal audit trail."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import PodSignal

router = APIRouter()


@router.get("/")
async def list_signals(
    session: Annotated[AsyncSession, Depends(get_session)],
    pod_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict]:
    stmt = select(PodSignal)
    if pod_name is not None:
        stmt = stmt.where(PodSignal.pod_name == pod_name)
    stmt = stmt.order_by(desc(PodSignal.created_at)).limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/{signal_id}")
async def get_signal(
    signal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(select(PodSignal).where(PodSignal.id == signal_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )
    return jsonable_encoder(row)

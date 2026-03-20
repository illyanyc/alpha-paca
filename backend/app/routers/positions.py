"""Open and historical positions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Position

router = APIRouter()


@router.get("/")
async def list_positions(
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[dict]:
    stmt = select(Position)
    if status_filter is not None:
        stmt = stmt.where(Position.status == status_filter)
    stmt = stmt.order_by(desc(Position.entry_time))
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [jsonable_encoder(r) for r in rows]


@router.get("/{position_id}")
async def get_position(
    position_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await session.execute(select(Position).where(Position.id == position_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Position not found",
        )
    return jsonable_encoder(row)

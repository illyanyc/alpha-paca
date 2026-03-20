"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session (same generator as `get_session`)."""
    async for session in get_session():
        yield session


def get_hot_config(request: Request) -> Any:
    """Hot-reloadable config service attached in `app.main` lifespan."""
    return request.app.state.hot_config

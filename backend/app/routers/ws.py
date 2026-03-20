"""WebSocket endpoints for live dashboard updates."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select

from app.db.engine import async_session_factory
from app.db.models import PortfolioState

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def portfolio_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(PortfolioState)
                    .order_by(desc(PortfolioState.updated_at))
                    .limit(1)
                )
                row = result.scalar_one_or_none()
            payload = {
                "type": "portfolio_state",
                "server_time": datetime.now(timezone.utc).isoformat(),
                "data": (
                    {
                        "id": str(row.id),
                        "state": row.state,
                        "nav": float(row.nav),
                        "cash": float(row.cash),
                        "equity": float(row.equity),
                        "drawdown_pct": float(row.drawdown_pct),
                    }
                    if row
                    else None
                ),
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(5.0)
    except WebSocketDisconnect:
        logger.debug("websocket client disconnected")
    except Exception as exc:
        logger.warning("websocket error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

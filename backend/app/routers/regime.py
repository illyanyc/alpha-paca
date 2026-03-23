"""Regime detection and health monitoring API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/current")
async def get_current_regime(request: Request) -> dict[str, Any]:
    """Return the current regime probabilities and dominant state."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return {"error": "orchestrator_not_initialized"}
    regime = getattr(orchestrator, "_current_regime", None)
    if regime is None:
        return {"dominant": "unknown", "probabilities": {}, "confidence": 0.0}
    return {
        "dominant": regime.dominant.value,
        "probabilities": regime.probabilities,
        "confidence": regime.confidence,
    }


@router.get("/history")
async def get_regime_history(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    """Return regime transition history from the database."""
    from sqlalchemy import select

    from app.db.engine import async_session_factory
    from app.db.models import RegimeHistory

    async with async_session_factory() as session:
        stmt = (
            select(RegimeHistory)
            .order_by(RegimeHistory.detected_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "dominant_regime": r.dominant_regime,
                "probabilities": r.probabilities,
                "confidence": float(r.confidence),
                "benchmark_symbol": r.benchmark_symbol,
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            }
            for r in rows
        ]

"""System health monitoring API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/circuit-breaker")
async def get_circuit_breaker_status(request: Request) -> dict[str, Any]:
    """Return the current circuit breaker state for all pods and system."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return {"error": "orchestrator_not_initialized"}
    cb = getattr(orchestrator, "_circuit_breaker", None)
    if cb is None:
        return {"error": "circuit_breaker_not_initialized"}
    return cb.get_status()


@router.get("/circuit-breaker/events")
async def get_circuit_breaker_events(
    request: Request, limit: int = 50
) -> list[dict[str, Any]]:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return []
    cb = getattr(orchestrator, "_circuit_breaker", None)
    if cb is None:
        return []
    return cb.get_recent_events(limit)


@router.get("/drift")
async def get_drift_status(request: Request) -> dict[str, Any]:
    """Return drift detection status for all pods."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return {"error": "orchestrator_not_initialized"}
    dd = getattr(orchestrator, "_drift_detector", None)
    if dd is None:
        return {"error": "drift_detector_not_initialized"}
    pod_stats = {}
    for pod_name in ["momentum", "mean_reversion", "event_driven", "sector_rotation", "stat_arb"]:
        pod_stats[pod_name] = dd.get_pod_stats(pod_name)
    return {"pods": pod_stats}


@router.get("/drift/events")
async def get_drift_events(
    request: Request, limit: int = 50
) -> list[dict[str, Any]]:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return []
    dd = getattr(orchestrator, "_drift_detector", None)
    if dd is None:
        return []
    return dd.get_recent_events(limit)


@router.get("/system")
async def get_system_health(request: Request) -> dict[str, Any]:
    """Composite system health check."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    scheduler = getattr(request.app.state, "trading_scheduler", None)

    health = {
        "api": "ok",
        "orchestrator": "ok" if orchestrator is not None else "unavailable",
        "scheduler": "ok" if scheduler is not None else "unavailable",
        "circuit_breaker": "unknown",
        "drift": "unknown",
        "regime": "unknown",
    }

    if orchestrator is not None:
        cb = getattr(orchestrator, "_circuit_breaker", None)
        if cb is not None:
            level = cb.system_level
            health["circuit_breaker"] = "ok" if level == 0 else f"level_{level}"

        regime = getattr(orchestrator, "_current_regime", None)
        if regime is not None:
            health["regime"] = regime.dominant.value

    return health

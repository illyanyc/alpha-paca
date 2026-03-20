"""Pod allocation and detail Pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PodAllocationOut(BaseModel):
    pod_name: str
    target_alloc_pct: float
    current_alloc_pct: float
    status: str
    sharpe_30d: float | None = None
    win_rate: float | None = None
    trade_count: int = 0
    ic_avg: float | None = None


class PodDetailOut(PodAllocationOut):
    recent_signals: list[dict[str, Any]] = []
    performance_summary: dict[str, Any] = {}

"""Signal-related Pydantic models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlphaScore(BaseModel):
    symbol: str
    score: float
    z_score: float = 0.0
    ic_weight: float = 0.0
    composite: float = 0.0


class PodSignalOut(BaseModel):
    """Pydantic representation of a pod signal — mirrors ``PodSignal`` ORM model."""

    id: uuid.UUID | None = Field(default_factory=uuid.uuid4)
    pod_name: str = ""
    symbol: str = ""
    signal_name: str = ""
    alpha_score: float = 0.0
    z_score: float = 0.0
    ic_weight: float = 0.0
    composite_score: float = 0.0
    side: str = "long"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    targets: dict[str, Any] | None = None
    position_size_pct: float = 0.0
    trade_type: str = "swing"
    urgency: str = "normal"
    reasoning: str | None = None
    created_at: datetime | None = None

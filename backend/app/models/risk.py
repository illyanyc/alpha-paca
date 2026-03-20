"""Risk-related Pydantic models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class VaROut(BaseModel):
    var_95: float
    cvar_95: float
    method: str = "historical"
    positions_count: int = 0
    calculated_at: datetime | None = None


class FactorExposureOut(BaseModel):
    market_beta: float = 0.0
    size_exposure: float = 0.0
    value_exposure: float = 0.0
    momentum_exposure: float = 0.0
    quality_exposure: float = 0.0
    low_vol_exposure: float = 0.0
    snapshot_time: datetime | None = None


class StressTestOut(BaseModel):
    scenario_name: str
    estimated_loss_pct: float
    estimated_loss_dollars: float
    positions_impacted: int = 0
    calculated_at: datetime | None = None


class RiskEventOut(BaseModel):
    id: uuid.UUID | None = None
    event_type: str
    severity: str
    description: str
    old_state: str | None = None
    new_state: str | None = None
    created_at: datetime | None = None


class DrawdownStateOut(BaseModel):
    intraday_dd_pct: float = 0.0
    daily_dd_pct: float = 0.0
    weekly_dd_pct: float = 0.0
    monthly_dd_pct: float = 0.0
    consecutive_losses: int = 0
    updated_at: datetime | None = None

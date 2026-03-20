"""Portfolio state Pydantic models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PortfolioStateOut(BaseModel):
    state: str
    nav: float
    cash: float
    equity: float
    gross_exposure_pct: float
    net_exposure_pct: float
    market_beta: float
    daily_pnl: float
    drawdown_pct: float
    updated_at: datetime | None = None

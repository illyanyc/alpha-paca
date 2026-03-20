"""Trade Pydantic models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class TradeOut(BaseModel):
    id: uuid.UUID | None = None
    symbol: str
    pod_name: str
    strategy_name: str = ""
    side: str
    entry_price: float
    exit_price: float | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    qty: float
    pnl: float | None = None
    pnl_pct: float | None = None
    slippage_entry_bps: float = 0.0
    slippage_exit_bps: float | None = None
    status: str = "open"


class TradeStats(BaseModel):
    total_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    profit_factor: float = 0.0

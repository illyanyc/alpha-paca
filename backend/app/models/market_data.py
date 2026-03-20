"""Market data Pydantic models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BarData(BaseModel):
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


class QuoteData(BaseModel):
    symbol: str
    price: float
    change_pct: float = 0.0
    volume: float = 0.0

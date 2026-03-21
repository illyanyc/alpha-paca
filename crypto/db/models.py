"""SQLAlchemy ORM models for crypto trading tables (all prefixed crypto_)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.engine import Base


class CryptoTrade(Base):
    __tablename__ = "crypto_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / closed / cancelled
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CryptoPosition(Base):
    __tablename__ = "crypto_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=0)
    market_value_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CryptoSignal(Base):
    __tablename__ = "crypto_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # technical / news / fundamental
    signal: Mapped[str] = mapped_column(String(20), nullable=False)  # strong_buy / buy / neutral / sell / strong_sell
    score: Mapped[float] = mapped_column(Float, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CryptoPrice(Base):
    __tablename__ = "crypto_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CryptoAgentHealth(Base):
    __tablename__ = "crypto_agent_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # healthy / degraded / crashed / restarted
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CryptoPortfolioState(Base):
    __tablename__ = "crypto_portfolio_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nav: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_exposure_pct: Mapped[float] = mapped_column(Float, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    realized_pnl_today: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0)
    high_water_mark: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    positions_count: Mapped[int] = mapped_column(Integer, default=0)
    is_halted: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

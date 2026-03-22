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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.engine import Base


class CryptoTrade(Base):
    __tablename__ = "crypto_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(10), nullable=False, default="swing", index=True)
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
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / closed / cancelled
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CryptoPosition(Base):
    __tablename__ = "crypto_positions"
    __table_args__ = (
        UniqueConstraint("pair", "bot_id", name="uq_position_pair_bot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(10), nullable=False, default="swing", index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="long")
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=0)
    market_value_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TradeJournalEntry(Base):
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    bot_id: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    indicators_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(30), nullable=True)
    price_at_decision: Mapped[float] = mapped_column(Float, nullable=False)
    portfolio_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    positions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_hold_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_hit_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_hit_stop: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


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

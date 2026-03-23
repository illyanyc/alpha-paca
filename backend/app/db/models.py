"""SQLAlchemy 2.0 ORM models for the AlphaPaca trading system.

All tables use UUID primary keys and UTC timestamps.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


# ---------------------------------------------------------------------------
# CORE
# ---------------------------------------------------------------------------


class PortfolioState(Base):
    __tablename__ = "portfolio_state"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String(32))
    nav: Mapped[float] = mapped_column(Numeric)
    cash: Mapped[float] = mapped_column(Numeric)
    equity: Mapped[float] = mapped_column(Numeric)
    gross_exposure_pct: Mapped[float] = mapped_column(Numeric)
    net_exposure_pct: Mapped[float] = mapped_column(Numeric)
    market_beta: Mapped[float] = mapped_column(Numeric)
    factor_exposures: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    daily_pnl: Mapped[float] = mapped_column(Numeric)
    intraday_high_water_mark: Mapped[float] = mapped_column(Numeric)
    drawdown_pct: Mapped[float] = mapped_column(Numeric)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_symbol", "symbol"),
        Index("ix_positions_pod_name", "pod_name"),
        Index("ix_positions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16))
    pod_name: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Numeric)
    entry_price: Mapped[float] = mapped_column(Numeric)
    current_price: Mapped[float] = mapped_column(Numeric)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric)
    factor_exposures: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Numeric)
    target_prices: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trade_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_symbol", "symbol"),
        Index("ix_orders_pod_name", "pod_name"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pod_signals.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    pod_name: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Numeric)
    limit_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fill_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    fill_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    slippage_bps: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    parent_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("orders.id"), nullable=True
    )

    signal: Mapped["PodSignal | None"] = relationship(lazy="selectin")
    parent: Mapped["Order | None"] = relationship(
        back_populates="children", remote_side="Order.id", foreign_keys=[parent_order_id]
    )
    children: Mapped[list["Order"]] = relationship(
        back_populates="parent", foreign_keys=[parent_order_id]
    )


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_symbol", "symbol"),
        Index("ix_trades_pod_name", "pod_name"),
        Index("ix_trades_entry_time", "entry_time"),
        Index("ix_trades_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16))
    pod_name: Mapped[str] = mapped_column(String(64))
    strategy_name: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Numeric)
    exit_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    qty: Mapped[float] = mapped_column(Numeric)
    pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    slippage_entry_bps: Mapped[float] = mapped_column(Numeric)
    slippage_exit_bps: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(String(32))


class PodAllocation(Base):
    __tablename__ = "pod_allocations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_name: Mapped[str] = mapped_column(String(64), unique=True)
    target_alloc_pct: Mapped[float] = mapped_column(Numeric)
    current_alloc_pct: Mapped[float] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(String(32))
    sharpe_30d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer)
    ic_avg: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PodSignal(Base):
    __tablename__ = "pod_signals"
    __table_args__ = (
        Index("ix_pod_signals_pod_name", "pod_name"),
        Index("ix_pod_signals_symbol", "symbol"),
        Index("ix_pod_signals_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    signal_name: Mapped[str] = mapped_column(String(64))
    alpha_score: Mapped[float] = mapped_column(Numeric)
    z_score: Mapped[float] = mapped_column(Numeric)
    ic_weight: Mapped[float] = mapped_column(Numeric)
    composite_score: Mapped[float] = mapped_column(Numeric)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Numeric)
    stop_loss: Mapped[float] = mapped_column(Numeric)
    targets: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    position_size_pct: Mapped[float] = mapped_column(Numeric)
    trade_type: Mapped[str] = mapped_column(String(32))
    urgency: Mapped[str] = mapped_column(String(32))
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PodPerformance(Base):
    __tablename__ = "pod_performance"
    __table_args__ = (
        Index("ix_pod_performance_pod_name", "pod_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_name: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pnl: Mapped[float] = mapped_column(Numeric)
    pnl_pct: Mapped[float] = mapped_column(Numeric)
    sharpe: Mapped[float] = mapped_column(Numeric)
    win_rate: Mapped[float] = mapped_column(Numeric)
    profit_factor: Mapped[float] = mapped_column(Numeric)
    max_drawdown: Mapped[float] = mapped_column(Numeric)
    trade_count: Mapped[int] = mapped_column(Integer)


class PodOverlap(Base):
    __tablename__ = "pod_overlap"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_a: Mapped[str] = mapped_column(String(64))
    pod_b: Mapped[str] = mapped_column(String(64))
    return_correlation: Mapped[float] = mapped_column(Numeric)
    holdings_overlap_pct: Mapped[float] = mapped_column(Numeric)
    shared_factor_exposure: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FactorExposure(Base):
    __tablename__ = "factor_exposures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    market_beta: Mapped[float] = mapped_column(Numeric)
    size_exposure: Mapped[float] = mapped_column(Numeric)
    value_exposure: Mapped[float] = mapped_column(Numeric)
    momentum_exposure: Mapped[float] = mapped_column(Numeric)
    quality_exposure: Mapped[float] = mapped_column(Numeric)
    low_vol_exposure: Mapped[float] = mapped_column(Numeric)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VarHistory(Base):
    __tablename__ = "var_history"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    var_95: Mapped[float] = mapped_column(Numeric)
    cvar_95: Mapped[float] = mapped_column(Numeric)
    method: Mapped[str] = mapped_column(String(32))
    positions_count: Mapped[int] = mapped_column(Integer)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StressTestResult(Base):
    __tablename__ = "stress_test_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scenario_name: Mapped[str] = mapped_column(String(128))
    estimated_loss_pct: Mapped[float] = mapped_column(Numeric)
    estimated_loss_dollars: Mapped[float] = mapped_column(Numeric)
    positions_impacted: Mapped[int] = mapped_column(Integer)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        Index("ix_risk_events_event_type", "event_type"),
        Index("ix_risk_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    old_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DrawdownState(Base):
    __tablename__ = "drawdown_state"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    intraday_dd_pct: Mapped[float] = mapped_column(Numeric)
    daily_dd_pct: Mapped[float] = mapped_column(Numeric)
    weekly_dd_pct: Mapped[float] = mapped_column(Numeric)
    monthly_dd_pct: Mapped[float] = mapped_column(Numeric)
    consecutive_losses: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RegimeHistory(Base):
    __tablename__ = "regime_history"
    __table_args__ = (
        Index("ix_regime_history_detected_at", "detected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dominant_regime: Mapped[str] = mapped_column(String(32))
    probabilities: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Numeric)
    benchmark_symbol: Mapped[str] = mapped_column(String(16))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DriftEventLog(Base):
    __tablename__ = "drift_events"
    __table_args__ = (
        Index("ix_drift_events_detected_at", "detected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    drift_score: Mapped[float] = mapped_column(Numeric)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CircuitBreakerLog(Base):
    __tablename__ = "circuit_breaker_log"
    __table_args__ = (
        Index("ix_circuit_breaker_log_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    prev_level: Mapped[str] = mapped_column(String(32))
    new_level: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategyEvolution(Base):
    __tablename__ = "strategy_evolution"
    __table_args__ = (
        Index("ix_strategy_evolution_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    generation: Mapped[int] = mapped_column(Integer)
    best_fitness: Mapped[float] = mapped_column(Numeric)
    avg_fitness: Mapped[float] = mapped_column(Numeric)
    best_genome: Mapped[dict] = mapped_column(JSONB)
    promoted_to_paper: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SignalICTracking(Base):
    __tablename__ = "signal_ic_tracking"
    __table_args__ = (
        Index("ix_signal_ic_tracking_pod_name", "pod_name"),
        Index("ix_signal_ic_tracking_signal_name", "signal_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    signal_name: Mapped[str] = mapped_column(String(64))
    pod_name: Mapped[str] = mapped_column(String(64))
    ic_value: Mapped[float] = mapped_column(Numeric)
    rolling_window_days: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    __table_args__ = (
        Index("ix_backtest_results_pod_name", "pod_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pod_name: Mapped[str] = mapped_column(String(64))
    signal_name: Mapped[str] = mapped_column(String(64))
    oos_win_rate: Mapped[float] = mapped_column(Numeric)
    oos_profit_factor: Mapped[float] = mapped_column(Numeric)
    oos_sharpe: Mapped[float] = mapped_column(Numeric)
    oos_max_drawdown: Mapped[float] = mapped_column(Numeric)
    oos_sample_count: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CorrelationMatrix(Base):
    __tablename__ = "correlation_matrix"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    matrix_data: Mapped[dict] = mapped_column(JSONB)
    symbols: Mapped[dict] = mapped_column(JSONB)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True)
    symbols: Mapped[dict] = mapped_column(JSONB)
    criteria: Mapped[dict] = mapped_column(JSONB)


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        Index("ix_watchlist_symbol", "symbol"),
        Index("ix_watchlist_pod_name", "pod_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16))
    pod_name: Mapped[str] = mapped_column(String(64))
    priority_score: Mapped[float] = mapped_column(Numeric)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text)


class NewsCache(Base):
    __tablename__ = "news_cache"
    __table_args__ = (
        Index("ix_news_cache_published_at", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    headline: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(128))
    symbols: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    catalyst_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScanResult(Base):
    __tablename__ = "scan_results"
    __table_args__ = (
        Index("ix_scan_results_pod_name", "pod_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scanner_name: Mapped[str] = mapped_column(String(64))
    pod_name: Mapped[str] = mapped_column(String(64))
    results: Mapped[dict] = mapped_column(JSONB)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentHealth(Base):
    __tablename__ = "agent_health"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_name: Mapped[str] = mapped_column(String(128), unique=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )


class ValidationLog(Base):
    __tablename__ = "validation_log"
    __table_args__ = (
        Index("ix_validation_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pod_signals.id"), nullable=True
    )
    validator_name: Mapped[str] = mapped_column(String(64))
    layer: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

"""PydanticAI-powered suggestions for tunable risk and execution parameters."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestResult, PodPerformance, PortfolioState, SignalICTracking, Trade

logger = structlog.get_logger(__name__)


class SettingSuggestion(BaseModel):
    key: str
    current_value: float
    suggested_value: float
    reason: str
    confidence: str = Field(description='One of "high", "medium", "low"')


class OptimizationResult(BaseModel):
    suggestions: list[SettingSuggestion]
    analysis_summary: str
    data_quality_notes: list[str]


async def _collect_trade_stats(session: AsyncSession, lookback_days: int) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    total = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.entry_time >= cutoff)
    )
    closed = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Trade.pnl), 0), func.avg(Trade.pnl)).where(
                Trade.entry_time >= cutoff,
                Trade.exit_time.isnot(None),
            )
        )
    ).one()
    win_count = await session.scalar(
        select(func.count()).where(
            Trade.entry_time >= cutoff,
            Trade.exit_time.isnot(None),
            Trade.pnl > 0,
        )
    )
    return {
        "trade_rows_in_window": int(total or 0),
        "closed_trades": int(closed[0] or 0),
        "sum_pnl_closed": float(closed[1] or 0),
        "avg_pnl_closed": float(closed[2] or 0),
        "wins_closed": int(win_count or 0),
    }


async def _collect_pod_performance(session: AsyncSession, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    stmt = select(PodPerformance).where(PodPerformance.period_end >= cutoff)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "pod_name": r.pod_name,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "pnl": float(r.pnl),
                "pnl_pct": float(r.pnl_pct),
                "sharpe": float(r.sharpe),
                "win_rate": float(r.win_rate),
                "profit_factor": float(r.profit_factor),
                "max_drawdown": float(r.max_drawdown),
                "trade_count": int(r.trade_count),
            }
        )
    return out


async def _collect_backtest_results(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = select(BacktestResult).order_by(BacktestResult.calculated_at.desc()).limit(200)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "pod_name": r.pod_name,
                "signal_name": r.signal_name,
                "oos_win_rate": float(r.oos_win_rate),
                "oos_profit_factor": float(r.oos_profit_factor),
                "oos_sharpe": float(r.oos_sharpe),
                "oos_max_drawdown": float(r.oos_max_drawdown),
                "oos_sample_count": int(r.oos_sample_count),
                "passed": bool(r.passed),
                "rejection_reason": r.rejection_reason,
                "calculated_at": r.calculated_at.isoformat(),
            }
        )
    return out


async def _collect_signal_ic(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = select(SignalICTracking).order_by(SignalICTracking.updated_at.desc()).limit(500)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "signal_name": r.signal_name,
                "pod_name": r.pod_name,
                "ic_value": float(r.ic_value),
                "rolling_window_days": int(r.rolling_window_days),
                "sample_count": int(r.sample_count),
                "updated_at": r.updated_at.isoformat(),
            }
        )
    return out


async def _collect_portfolio_state(session: AsyncSession) -> dict[str, Any] | None:
    stmt = select(PortfolioState).order_by(PortfolioState.updated_at.desc()).limit(1)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return {
        "state": row.state,
        "nav": float(row.nav),
        "cash": float(row.cash),
        "equity": float(row.equity),
        "gross_exposure_pct": float(row.gross_exposure_pct),
        "net_exposure_pct": float(row.net_exposure_pct),
        "market_beta": float(row.market_beta),
        "daily_pnl": float(row.daily_pnl),
        "drawdown_pct": float(row.drawdown_pct),
        "updated_at": row.updated_at.isoformat(),
    }


def _collect_current_settings(hot_config: Any) -> dict[str, Any]:
    return hot_config.get_all()


SYSTEM_PROMPT = """You are a quantitative portfolio engineer for a multi-pod US equity system.
Given JSON snapshots of live trades, pod performance, backtests, signal IC history, portfolio state,
and current tunable parameters, propose conservative adjustments to numeric settings.
Rules:
- Only suggest keys you are confident about; omit uncertain changes (the schema still allows empty suggestions).
- Respect min/max bounds mentally; suggested values must stay plausible for the described ranges.
- Prefer stability: small deltas unless data strongly supports larger moves.
- confidence must be exactly "high", "medium", or "low".
Return structured output matching OptimizationResult.
"""


async def run_optimization(
    hot_config: Any,
    db_session: AsyncSession,
    lookback_days: int = 30,
) -> OptimizationResult:
    notes: list[str] = []
    trade_stats = await _collect_trade_stats(db_session, lookback_days)
    if trade_stats["trade_rows_in_window"] == 0:
        notes.append("No trades in lookback window — suggestions will be speculative.")

    pod_perf = await _collect_pod_performance(db_session, lookback_days)
    if not pod_perf:
        notes.append("No pod_performance rows in lookback window.")

    backtests = await _collect_backtest_results(db_session)
    if not backtests:
        notes.append("No backtest_results rows available.")

    signal_ic = await _collect_signal_ic(db_session)
    if not signal_ic:
        notes.append("No signal_ic_tracking rows available.")

    portfolio = await _collect_portfolio_state(db_session)
    if portfolio is None:
        notes.append("portfolio_state is empty.")

    current_settings = _collect_current_settings(hot_config)

    payload = {
        "lookback_days": lookback_days,
        "trade_stats": trade_stats,
        "pod_performance": pod_perf,
        "backtest_results": backtests,
        "signal_ic": signal_ic,
        "portfolio_state": portfolio,
        "current_settings": current_settings,
    }
    user_prompt = (
        "Analyze the following JSON data and produce OptimizationResult JSON-compatible output.\n\n"
        f"{json.dumps(payload, default=str, indent=2)}"
    )

    agent = Agent(
        "anthropic:claude-sonnet-4-20250514",
        output_type=OptimizationResult,
        system_prompt=SYSTEM_PROMPT,
    )
    try:
        run = await agent.run(user_prompt)
        result = run.output
        merged_notes = list(result.data_quality_notes)
        for n in notes:
            if n not in merged_notes:
                merged_notes.append(n)
        return result.model_copy(update={"data_quality_notes": merged_notes})
    except Exception as exc:
        logger.exception("settings_optimizer_failed", error=str(exc))
        return OptimizationResult(
            suggestions=[],
            analysis_summary=f"Optimization agent failed: {exc!s}",
            data_quality_notes=[*notes, str(exc)],
        )

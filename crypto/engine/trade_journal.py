"""Trade Journal — logs every AI decision with full context for backtesting and analysis.

HOLDs are sampled at 1-in-10 to avoid DB bloat. BUY/SELL always logged.
Outcomes are filled in later when trades resolve.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update

from db.engine import async_session_factory
from db.models import TradeJournalEntry

logger = structlog.get_logger(__name__)

HOLD_SAMPLE_RATE = 10


async def log_decision(
    bot_id: str,
    pair: str,
    action: str,
    conviction: float,
    price_at_decision: float,
    reasoning: str = "",
    target_price: float | None = None,
    stop_price: float | None = None,
    indicators: dict | None = None,
    regime: str | None = None,
    portfolio_state: dict | None = None,
    positions: list | None = None,
) -> int | None:
    """Log a bot decision to the trade_journal table. Returns the entry id."""
    if action == "HOLD" and random.randint(1, HOLD_SAMPLE_RATE) != 1:
        return None

    try:
        entry = TradeJournalEntry(
            timestamp=datetime.now(timezone.utc),
            bot_id=bot_id,
            pair=pair,
            action=action,
            conviction=conviction,
            target_price=target_price,
            stop_price=stop_price,
            reasoning=reasoning[:2000] if reasoning else None,
            indicators_json=json.dumps(indicators)[:4000] if indicators else None,
            regime=regime,
            price_at_decision=price_at_decision,
            portfolio_state_json=json.dumps(portfolio_state)[:2000] if portfolio_state else None,
            positions_json=json.dumps(positions, default=str)[:2000] if positions else None,
        )
        async with async_session_factory() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry.id
    except Exception as e:
        logger.warning("journal_log_failed", error=str(e))
        return None


async def fill_outcome(
    journal_id: int,
    pnl: float,
    pnl_pct: float,
    hold_minutes: float,
    hit_target: bool,
    hit_stop: bool,
) -> None:
    """Fill in the outcome columns for a previously logged decision."""
    try:
        async with async_session_factory() as session:
            stmt = (
                update(TradeJournalEntry)
                .where(TradeJournalEntry.id == journal_id)
                .values(
                    outcome_pnl=pnl,
                    outcome_pnl_pct=pnl_pct,
                    outcome_hold_minutes=hold_minutes,
                    outcome_hit_target=hit_target,
                    outcome_hit_stop=hit_stop,
                )
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as e:
        logger.warning("journal_fill_outcome_failed", id=journal_id, error=str(e))


async def get_recent_entries(
    bot_id: str | None = None,
    days: int = 7,
    limit: int = 500,
) -> list[dict]:
    """Fetch recent journal entries for analysis."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with async_session_factory() as session:
            stmt = (
                select(TradeJournalEntry)
                .where(TradeJournalEntry.timestamp >= cutoff)
                .order_by(TradeJournalEntry.timestamp.desc())
                .limit(limit)
            )
            if bot_id:
                stmt = stmt.where(TradeJournalEntry.bot_id == bot_id)
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "bot_id": e.bot_id,
                    "pair": e.pair,
                    "action": e.action,
                    "conviction": e.conviction,
                    "target_price": e.target_price,
                    "stop_price": e.stop_price,
                    "reasoning": e.reasoning,
                    "regime": e.regime,
                    "price_at_decision": e.price_at_decision,
                    "outcome_pnl": e.outcome_pnl,
                    "outcome_pnl_pct": e.outcome_pnl_pct,
                    "outcome_hold_minutes": e.outcome_hold_minutes,
                    "outcome_hit_target": e.outcome_hit_target,
                    "outcome_hit_stop": e.outcome_hit_stop,
                }
                for e in entries
            ]
    except Exception as e:
        logger.warning("journal_fetch_failed", error=str(e))
        return []

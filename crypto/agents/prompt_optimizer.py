"""PromptOptimizer — Claude-powered meta-agent that analyzes trade journal
and backtest results, then generates prompt refinements (learnings).

Runs once daily after the backtest completes. Learnings are stored in Redis
and injected into bot prompts on each tick. Additive only — never rewrites
core rules, only appends learnings.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

logger = structlog.get_logger(__name__)

LEARNINGS_KEY_TEMPLATE = "crypto:learnings:{bot_id}"
MAX_LEARNINGS = 20


class PromptLearning(BaseModel):
    bot_id: str
    learning: str
    confidence: float
    sample_size: int


class OptimizerOutput(BaseModel):
    learnings: list[PromptLearning]
    summary: str


_OPTIMIZER_INSTRUCTIONS = (
    "You are a trading system analyst. You review trade journal entries and backtest "
    "metrics to identify systematic patterns — both failures and successes.\n\n"
    "Your job is to generate SPECIFIC, ACTIONABLE learnings that can be injected into "
    "trading bot prompts to improve future performance.\n\n"
    "## Rules\n"
    "- Each learning must be specific (mention exact pairs, time windows, indicator values)\n"
    "- Only suggest learnings backed by data (minimum 3 data points)\n"
    "- Assign a confidence score based on sample size and consistency\n"
    "- Focus on failure patterns first, then optimization opportunities\n"
    "- Keep learnings concise (1-2 sentences each)\n"
    "- Never suggest removing core safety rules\n\n"
    "## Examples of Good Learnings\n"
    "- 'Avoid BTC entries between 00:00-06:00 UTC (70% stop-out rate, n=7)'\n"
    "- 'SOL has 30% win rate — require conviction >= 0.85 (n=10)'\n"
    "- 'When RSI > 75 on 4H, swing entries have 25% success — wait for pullback (n=8)'\n"
    "- 'VWAP mean-reversion on ETH works best when ATR < 2% (win rate 80%, n=5)'\n"
)

_optimizer_agent: Agent | None = None


def _get_optimizer_agent() -> Agent:
    global _optimizer_agent
    if _optimizer_agent is None:
        _optimizer_agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=_OPTIMIZER_INSTRUCTIONS,
            output_type=OptimizerOutput,
        )
    return _optimizer_agent


async def run_prompt_optimization(
    journal_entries: list[dict],
    backtest_metrics: dict[str, Any],
    current_learnings: dict[str, list[str]],
) -> OptimizerOutput:
    """Analyze journal + backtest results and generate prompt refinements."""
    prompt_sections = []

    prompt_sections.append("## Trade Journal (last 7 days)")

    for bot_id in ("swing", "day"):
        bot_entries = [e for e in journal_entries if e.get("bot_id") == bot_id]
        if not bot_entries:
            prompt_sections.append(f"\n### {bot_id}: No entries")
            continue

        trades = [e for e in bot_entries if e["action"] != "HOLD"]
        holds = [e for e in bot_entries if e["action"] == "HOLD"]

        wins = [e for e in trades if (e.get("outcome_pnl") or 0) > 0]
        losses = [e for e in trades if (e.get("outcome_pnl") or 0) < 0]
        pending = [e for e in trades if e.get("outcome_pnl") is None]

        prompt_sections.append(
            f"\n### {bot_id}: {len(trades)} trades, {len(wins)} wins, "
            f"{len(losses)} losses, {len(pending)} pending, {len(holds)} holds"
        )

        for e in trades[:30]:
            pnl_str = f"PnL={e.get('outcome_pnl_pct', '?')}%" if e.get("outcome_pnl_pct") is not None else "pending"
            hit = ""
            if e.get("outcome_hit_target"):
                hit = " [HIT TARGET]"
            elif e.get("outcome_hit_stop"):
                hit = " [HIT STOP]"
            prompt_sections.append(
                f"  {e.get('pair')} {e['action']} conv={e.get('conviction', 0):.2f} "
                f"price=${e.get('price_at_decision', 0):,.2f} regime={e.get('regime', '?')} "
                f"→ {pnl_str}{hit} | {(e.get('reasoning') or '')[:80]}"
            )

    if backtest_metrics:
        prompt_sections.append("\n## Backtest Metrics")
        for bot_id, metrics in backtest_metrics.items():
            if isinstance(metrics, dict):
                prompt_sections.append(
                    f"  {bot_id}: return={metrics.get('total_return_pct', 0):.1f}%, "
                    f"sharpe={metrics.get('sharpe_ratio', 0):.2f}, "
                    f"win_rate={metrics.get('win_rate', 0):.0%}, "
                    f"max_dd={metrics.get('max_drawdown_pct', 0):.1f}%, "
                    f"trades={metrics.get('total_trades', 0)}, "
                    f"false_entries={metrics.get('false_entries', 0)}"
                )

    if current_learnings:
        prompt_sections.append("\n## Current Active Learnings")
        for bot_id, items in current_learnings.items():
            for item in items:
                prompt_sections.append(f"  [{bot_id}] {item}")

    prompt_sections.append(
        "\n## Task\n"
        "Analyze the above data. Identify systematic patterns and generate specific, "
        "actionable learnings for each bot. Each learning should reference concrete data "
        "(pairs, time windows, indicator thresholds, win rates) from the journal.\n"
        "Only include learnings you're confident about (backed by 3+ data points).\n"
        "Mark bot_id as 'swing' or 'day' for each learning."
    )

    prompt = "\n".join(prompt_sections)

    agent = _get_optimizer_agent()
    result = await agent.run(prompt)
    return result.output


async def store_learnings(
    redis_conn: aioredis.Redis,
    output: OptimizerOutput,
    min_confidence: float = 0.6,
) -> dict[str, int]:
    """Store approved learnings in Redis, capping at MAX_LEARNINGS per bot."""
    stored: dict[str, int] = {}
    for learning in output.learnings:
        if learning.confidence < min_confidence:
            continue

        key = LEARNINGS_KEY_TEMPLATE.format(bot_id=learning.bot_id)
        existing_raw = await redis_conn.get(key)
        existing = json.loads(existing_raw) if existing_raw else []

        if learning.learning not in existing:
            existing.append(learning.learning)

        existing = existing[-MAX_LEARNINGS:]

        await redis_conn.set(key, json.dumps(existing))
        stored[learning.bot_id] = stored.get(learning.bot_id, 0) + 1

    logger.info("learnings_stored", counts=stored, summary=output.summary[:120])
    return stored


async def get_current_learnings(redis_conn: aioredis.Redis) -> dict[str, list[str]]:
    """Fetch current learnings for all bots."""
    result = {}
    for bot_id in ("swing", "day"):
        key = LEARNINGS_KEY_TEMPLATE.format(bot_id=bot_id)
        raw = await redis_conn.get(key)
        result[bot_id] = json.loads(raw) if raw else []
    return result

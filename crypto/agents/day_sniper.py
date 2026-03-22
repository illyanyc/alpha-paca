"""DaySniper — precision intraday scalp bot for crypto.

Evaluates 1m/5m candles, captures momentum and mean-reversion setups.
Uses PydanticAI + Claude for decisions. Runs every ~30 seconds.
Flat overnight — closes all positions before end of trading window.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from agents.base import BaseAgent
from config import get_settings

logger = structlog.get_logger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "skills" / "crypto_day_sniper.md"
LEARNINGS_KEY = "crypto:learnings:day"


class DayDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    pair: str
    conviction: float
    target_price: float
    stop_price: float
    timeframe_minutes: int
    reasoning: str


class DaySniperOutput(BaseModel):
    decisions: list[DayDecision]
    market_outlook: str
    summary: str


class DaySniperAgent(BaseAgent):
    name = "day_sniper"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._min_conviction = settings.crypto.min_conviction

        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=skill_text,
            output_type=DaySniperOutput,
        )

    async def run(self, **kwargs) -> dict:
        r = await self._get_redis()

        indicators = kwargs.get("indicators", {})
        regime = kwargs.get("regime", {})
        positions = kwargs.get("positions", [])
        portfolio = kwargs.get("portfolio", {})
        candles_5m = kwargs.get("candles_5m", {})
        candles_1m = kwargs.get("candles_1m", {})
        prices = kwargs.get("prices", {})

        learnings_raw = await r.get(LEARNINGS_KEY)
        learnings = json.loads(learnings_raw) if learnings_raw else []

        self.think("Scanning 1m/5m charts for intraday setups...")

        prompt = self._build_prompt(
            indicators=indicators,
            regime=regime,
            positions=positions,
            portfolio=portfolio,
            candles_5m=candles_5m,
            candles_1m=candles_1m,
            prices=prices,
            learnings=learnings,
        )

        result = await self._agent.run(prompt)
        output = result.output

        self.think(f"Day outlook: {output.market_outlook}")

        actionable = [
            d for d in output.decisions
            if d.action != "HOLD" and d.conviction >= self._min_conviction
        ]

        for d in output.decisions:
            tag = ">>>" if d in actionable else "---"
            self.think(f"{tag} {d.pair}: {d.action} conv={d.conviction:.2f} tgt=${d.target_price:,.2f} stp=${d.stop_price:,.2f} {d.timeframe_minutes}m — {d.reasoning[:60]}")

        if actionable:
            self.think(f"Day: {len(actionable)} scalp(s) ready")
        else:
            self.think(f"Day: HOLD on all {len(output.decisions)} pairs")

        return {
            "decisions": [d.model_dump() for d in actionable],
            "all_decisions": [d.model_dump() for d in output.decisions],
            "market_outlook": output.market_outlook,
            "summary": output.summary,
        }

    def _build_prompt(
        self,
        indicators: dict,
        regime: dict,
        positions: list,
        portfolio: dict,
        candles_5m: dict,
        candles_1m: dict,
        prices: dict,
        learnings: list,
    ) -> str:
        settings = get_settings()
        sections: list[str] = []

        sections.append(f"## Tracked Pairs: {', '.join(settings.crypto.pair_list)}")

        nav = portfolio.get("nav", 0)
        cap = settings.crypto.max_capital
        cap_label = f"${nav:,.2f} (whole account)" if cap <= 0 else f"${cap:,.0f}"
        sections.append(f"## Capital: {cap_label}")

        if portfolio:
            sections.append(
                f"## Portfolio\nNAV: ${nav:,.2f} | Cash: ${portfolio.get('cash', 0):,.2f} | "
                f"Exposure: {portfolio.get('total_exposure_pct', 0):.1f}% | "
                f"Daily PnL: ${portfolio.get('realized_pnl_today', 0):+,.2f}"
            )

        day_positions = [p for p in positions if p.get("bot_id") == "day"]
        if day_positions:
            pos_lines = ["## Current Day Positions"]
            for p in day_positions:
                pair = p.get("pair", "?")
                entry = float(p.get("avg_entry_price", 0))
                current = float(p.get("current_price", 0))
                pnl_pct = float(p.get("unrealized_pnl_pct", 0))
                pos_lines.append(
                    f"- {pair}: entry=${entry:,.2f}, now=${current:,.2f}, PnL={pnl_pct:+.1f}%"
                )
            sections.append("\n".join(pos_lines))
        else:
            sections.append("## Current Day Positions\nNONE — flat.")

        if regime:
            sections.append(
                f"## Regime: [{regime.get('label', '?')}] (conf={regime.get('confidence', 0):.0%})"
            )

        for pair in settings.crypto.pair_list:
            pair_ind = indicators.get(pair, {})
            if pair_ind:
                sections.append(
                    f"## {pair} — 5m Indicators\n"
                    f"RSI={pair_ind.get('rsi', 'N/A')}, "
                    f"MACD={pair_ind.get('macd', 'N/A')}, "
                    f"MACD_signal={pair_ind.get('macd_signal', 'N/A')}, "
                    f"BB_upper={pair_ind.get('bb_upper', 'N/A')}, "
                    f"BB_lower={pair_ind.get('bb_lower', 'N/A')}, "
                    f"VWAP={pair_ind.get('vwap', 'N/A')}, "
                    f"ATR={pair_ind.get('atr', 'N/A')}"
                )

            pair_5m = candles_5m.get(pair, [])
            if pair_5m:
                recent = pair_5m[-20:]
                candle_lines = [f"## {pair} — Recent 5m Candles (last {len(recent)})"]
                for c in recent:
                    candle_lines.append(
                        f"  O={c.get('open', 0):,.2f} H={c.get('high', 0):,.2f} "
                        f"L={c.get('low', 0):,.2f} C={c.get('close', 0):,.2f} "
                        f"V={c.get('volume', 0):,.0f}"
                    )
                sections.append("\n".join(candle_lines))

            pair_1m = candles_1m.get(pair, [])
            if pair_1m:
                recent = pair_1m[-20:]
                candle_lines = [f"## {pair} — Recent 1m Candles (last {len(recent)})"]
                for c in recent:
                    candle_lines.append(
                        f"  O={c.get('open', 0):,.2f} H={c.get('high', 0):,.2f} "
                        f"L={c.get('low', 0):,.2f} C={c.get('close', 0):,.2f} "
                        f"V={c.get('volume', 0):,.0f}"
                    )
                sections.append("\n".join(candle_lines))

            pair_price = prices.get(pair, {})
            if pair_price:
                sections.append(
                    f"## {pair} — Current\n"
                    f"Mid=${pair_price.get('mid', 0):,.2f}, "
                    f"Spread={pair_price.get('spread_bps', 0):.1f}bps"
                )

        if learnings:
            learning_lines = ["## Recent Learnings (auto-generated from backtest analysis)"]
            for l_item in learnings[:15]:
                learning_lines.append(f"- {l_item}")
            sections.append("\n".join(learning_lines))

        sections.append(
            f"\nProvide BUY/SELL/HOLD for each pair. "
            f"Min conviction: {self._min_conviction}. "
            f"Min R/R: {settings.crypto.day_min_rr_ratio}:1. "
            f"Max hold: {settings.crypto.day_max_hold_hours}h. "
            f"HOLD is safe — only scalp clear, high-volume setups."
        )

        return "\n\n".join(sections)

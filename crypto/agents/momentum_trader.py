"""MomentumTrader — deterministic 4H Adaptive Momentum strategy agent.

Replaces DaySniperAgent.  Uses the AdaptiveMomentumStrategy composite
scoring engine (no LLM for entry/exit decisions).  Evaluates every 60s,
produces BUY/SELL/HOLD per pair based on -100..+100 composite score.
"""

from __future__ import annotations

from typing import Any

import structlog

from agents.base import BaseAgent
from config import get_settings
from engine.strategies import AdaptiveMomentumStrategy, ScoreBreakdown

logger = structlog.get_logger(__name__)


class MomentumDecision:
    """Structured decision output — mirrors the old DayDecision schema."""
    __slots__ = (
        "action", "pair", "conviction", "target_price", "stop_price",
        "reasoning", "composite_score", "tech_conditions",
    )

    def __init__(
        self,
        action: str,
        pair: str,
        conviction: float,
        target_price: float,
        stop_price: float,
        reasoning: str,
        composite_score: float = 0.0,
        tech_conditions: dict[str, bool] | None = None,
    ) -> None:
        self.action = action
        self.pair = pair
        self.conviction = conviction
        self.target_price = target_price
        self.stop_price = stop_price
        self.reasoning = reasoning
        self.composite_score = composite_score
        self.tech_conditions = tech_conditions or {}

    def model_dump(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "pair": self.pair,
            "conviction": self.conviction,
            "target_price": self.target_price,
            "stop_price": self.stop_price,
            "reasoning": self.reasoning,
            "composite_score": self.composite_score,
            "tech_conditions": self.tech_conditions,
        }


class MomentumTraderAgent(BaseAgent):
    name = "momentum"

    def __init__(self) -> None:
        super().__init__()
        self._strategy = AdaptiveMomentumStrategy()

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        buy_threshold = settings.crypto.composite_buy_threshold
        exit_threshold = settings.crypto.composite_exit_threshold
        atr_stop_mult = settings.crypto.atr_stop_multiplier
        atr_tp_mult = settings.crypto.atr_tp_multiplier

        indicators_4h = kwargs.get("indicators_4h", {})
        indicators_daily = kwargs.get("indicators_daily", {})
        news_data = kwargs.get("news_data", {})
        onchain_data = kwargs.get("onchain", {})
        microstructure = kwargs.get("microstructure", {})
        positions = kwargs.get("positions", [])
        prices = kwargs.get("prices", {})

        all_decisions: list[MomentumDecision] = []
        actionable: list[MomentumDecision] = []

        self.think("Evaluating Adaptive Momentum strategy across pairs...")

        for pair in settings.crypto.pair_list:
            pair_ind_4h = indicators_4h.get(pair, {})
            pair_ind_daily = indicators_daily.get(pair, {})
            pair_micro = microstructure.get(pair, {}) if isinstance(microstructure, dict) else {}

            if not pair_ind_4h:
                all_decisions.append(MomentumDecision(
                    action="HOLD", pair=pair, conviction=0.0,
                    target_price=0.0, stop_price=0.0,
                    reasoning="No 4H indicators available",
                ))
                continue

            breakdown: ScoreBreakdown = self._strategy.evaluate(
                indicators_4h=pair_ind_4h,
                indicators_daily=pair_ind_daily if pair_ind_daily else None,
                news_data=news_data if news_data else None,
                onchain_data=onchain_data if onchain_data else None,
                microstructure=pair_micro if pair_micro else None,
                buy_threshold=buy_threshold,
                exit_threshold=exit_threshold,
            )

            atr_val = pair_ind_4h.get("atr", 0)
            close_price = pair_ind_4h.get("close", 0)
            price_data = prices.get(pair, {})
            mid_price = price_data.get("mid", close_price) if price_data else close_price

            if mid_price <= 0:
                mid_price = close_price

            has_position = any(
                p.get("pair") == pair
                and p.get("bot_id") in ("momentum", "day")
                and float(p.get("qty", 0)) > 0
                for p in positions
            )

            if atr_val and atr_val > 0 and mid_price > 0:
                target_price = mid_price + atr_val * atr_tp_mult
                stop_price = mid_price - atr_val * atr_stop_mult
            else:
                target_price = mid_price * 1.03
                stop_price = mid_price * 0.97

            all_tech_met = all(breakdown.tech_conditions.values()) if breakdown.tech_conditions else False
            conviction = min(1.0, abs(breakdown.composite) / 100.0)

            if breakdown.composite >= buy_threshold and not has_position:
                action = "BUY"
                if all_tech_met:
                    conviction = min(1.0, conviction * 1.2)
            elif breakdown.composite <= exit_threshold and has_position:
                action = "SELL"
                conviction = min(1.0, abs(breakdown.composite) / 80.0)
            else:
                action = "HOLD"

            reasoning = " | ".join(breakdown.reasons)
            reasoning = f"[{breakdown.composite:+.0f}] {reasoning}"

            decision = MomentumDecision(
                action=action,
                pair=pair,
                conviction=round(conviction, 3),
                target_price=round(target_price, 2),
                stop_price=round(stop_price, 2),
                reasoning=reasoning,
                composite_score=breakdown.composite,
                tech_conditions=breakdown.tech_conditions,
            )
            all_decisions.append(decision)

            if action != "HOLD" and conviction >= settings.crypto.min_conviction:
                actionable.append(decision)

            tag = ">>>" if decision in actionable else "---"
            self.think(
                f"{tag} {pair}: {action} score={breakdown.composite:+.0f} "
                f"conv={conviction:.2f} "
                f"T={breakdown.technical:.0f}/S={breakdown.sentiment:+.0f}/O={breakdown.onchain:+.0f}"
            )

        if actionable:
            self.think(f"Momentum: {len(actionable)} actionable signal(s)")
        else:
            self.think(f"Momentum: HOLD on all {len(all_decisions)} pairs")

        return {
            "decisions": [d.model_dump() for d in actionable],
            "all_decisions": [d.model_dump() for d in all_decisions],
            "market_outlook": self._summarize_outlook(all_decisions),
            "summary": f"{len(actionable)} actionable of {len(all_decisions)} pairs evaluated",
        }

    @staticmethod
    def _summarize_outlook(decisions: list[MomentumDecision]) -> str:
        scores = [d.composite_score for d in decisions if d.composite_score != 0]
        if not scores:
            return "No data"
        avg = sum(scores) / len(scores)
        if avg > 30:
            return f"Strongly bullish (avg score {avg:+.0f})"
        if avg > 10:
            return f"Bullish (avg score {avg:+.0f})"
        if avg < -20:
            return f"Bearish (avg score {avg:+.0f})"
        return f"Neutral (avg score {avg:+.0f})"

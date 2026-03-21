"""OrchestratorAgent — combines all signals, decides entry/exit via PydanticAI + Claude."""

from __future__ import annotations

import json
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from agents.base import BaseAgent
from config import get_settings

logger = structlog.get_logger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "skills" / "crypto_orchestrator.md"
TECH_SIGNAL_KEY = "crypto:signals:technical"
FUND_SIGNAL_KEY = "crypto:signals:fundamental"
NEWS_CACHE_KEY = "crypto:news:sentiment"
BACKTEST_KEY = "crypto:backtest:results"
STRATEGY_SIGNALS_KEY = "crypto:signals:strategies"


class TradeDecision(BaseModel):
    action: str  # BUY / SELL / HOLD
    pair: str
    size_pct: float  # % of capital to allocate
    confidence: float  # 0.0 to 1.0
    reasoning: str
    urgency: str  # immediate / normal / low


class OrchestratorOutput(BaseModel):
    decisions: list[TradeDecision]
    market_outlook: str  # bullish / bearish / neutral / mixed
    summary: str


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()

        skill_text = ""
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text()

        self._confidence_threshold = settings.crypto.confidence_threshold

        self._agent = Agent(
            "anthropic:claude-sonnet-4-20250514",
            instructions=(
                "You are an AGGRESSIVE crypto portfolio manager. Deploy capital or protect it.\n\n"
                "You receive technical, fundamental, news, and strategy signals for each pair. "
                "Decide BUY / SELL / HOLD with position sizing.\n\n"
                "## POSITION SEMANTICS (Coinbase spot — long-only)\n"
                "- BUY = enter or add to a LONG position (you own the crypto)\n"
                "- SELL = EXIT an existing long position back to USD (not shorting)\n"
                "- HOLD = no change to current position\n\n"
                "## WHEN TO BUY\n"
                f"- Confidence threshold: {self._confidence_threshold}\n"
                "- If 2+ signal sources (tech, fundamental, news, strategies) are bullish → BUY.\n"
                "- If all sources agree bullish → confidence >= 0.8, size 15-25%.\n"
                "- If 2 sources agree → confidence >= 0.6, size 5-15%.\n"
                "- When macro is neutral-to-bullish, deploy at least 30-50% of capital.\n"
                "- Idle cash earns nothing. Prefer small positions over none.\n\n"
                "## WHEN TO SELL (EXIT)\n"
                "- You MUST output SELL for a pair if you hold a position AND:\n"
                "  * Technical signal is SELL or STRONG_SELL (bearish indicators)\n"
                "  * Fundamental signal is SELL or STRONG_SELL\n"
                "  * News is bearish with score < -0.3\n"
                "  * 2+ signal sources turn negative on a held pair\n"
                "  * Unrealized PnL is worse than -3% (stop-loss territory)\n"
                "  * Unrealized PnL exceeds +8% and signals are weakening (take-profit)\n"
                "- SELL decisions should have confidence >= 0.5 — be DECISIVE about exits.\n"
                "- Protecting capital from drawdowns is as important as entering trades.\n"
                "- If you have NO position in a pair and signals are bearish, output HOLD.\n\n"
                "## GENERAL\n"
                "- Check 'Current Positions' to know what you hold. Only SELL pairs you own.\n"
                "- Only BUY pairs with no/small position when signals are bullish.\n"
                "- Size proportionally: high conviction = 15-25%, moderate = 5-15%.\n"
                "- Provide clear reasoning for every decision.\n"
                "- DO NOT default everything to HOLD — that is failure.\n\n"
                f"{skill_text}"
            ),
            output_type=OrchestratorOutput,
        )

    async def run(self, **kwargs) -> dict:
        settings = get_settings()
        r = await self._get_redis()

        self.think("Gathering signals from tech, fundamental, news, and strategies...")

        tech_raw = await r.get(TECH_SIGNAL_KEY)
        fund_raw = await r.get(FUND_SIGNAL_KEY)
        news_raw = await r.get(NEWS_CACHE_KEY)
        backtest_raw = await r.get(BACKTEST_KEY)
        strat_raw = await r.get(STRATEGY_SIGNALS_KEY)

        tech_signals = json.loads(tech_raw) if tech_raw else {}
        fund_signals = json.loads(fund_raw) if fund_raw else {}
        news_data = json.loads(news_raw) if news_raw else {}
        backtest_data = json.loads(backtest_raw) if backtest_raw else {}
        strategy_signals = json.loads(strat_raw) if strat_raw else {}

        positions_raw = kwargs.get("positions", [])
        portfolio_state = kwargs.get("portfolio_state", {})
        learning_summary = kwargs.get("learning_summary", {})

        tech_summary = ", ".join(f"{p}: {d.get('signal', '?')}" for p, d in tech_signals.items()) or "none"
        self.think(f"Tech signals: {tech_summary}")

        news_sentiment = news_data.get("overall_sentiment", "?") if isinstance(news_data, dict) else "?"
        news_score = news_data.get("overall_score", 0) if isinstance(news_data, dict) else 0
        self.think(f"News: {news_sentiment} (score={news_score:.2f})")

        if backtest_data.get("strategy_weights"):
            weights = backtest_data["strategy_weights"]
            best = max(weights, key=weights.get) if weights else "?"
            self.think(f"Best strategy (backtest): {best} ({weights.get(best, 0):.0%})")

        if strategy_signals:
            for pair, strats in list(strategy_signals.items())[:3]:
                buy_strats = [s["name"] for s in strats if s.get("signal") == "buy"]
                if buy_strats:
                    self.think(f"Strategy BUY signals for {pair}: {', '.join(buy_strats)}")

        self.think("Sending combined data to AI for trade decisions...")

        prompt = self._build_prompt(
            tech_signals, fund_signals, news_data, positions_raw, portfolio_state,
            settings, backtest_data, strategy_signals, learning_summary,
        )

        result = await self._agent.run(prompt)
        output = result.output

        self.think(f"Market outlook: {output.market_outlook}")

        actionable = [
            d for d in output.decisions
            if d.action != "HOLD" and d.confidence >= self._confidence_threshold
        ]

        for d in output.decisions:
            tag = "✅" if d in [ad for ad in output.decisions if ad.action != "HOLD" and ad.confidence >= self._confidence_threshold] else "⏭️"
            self.think(f"{tag} {d.pair}: {d.action} conf={d.confidence:.2f} — {d.reasoning[:80]}")

        if actionable:
            self.think(f"Executing {len(actionable)} trade(s)")
        else:
            self.think(f"No trades — all {len(output.decisions)} decisions below threshold ({self._confidence_threshold})")

        logger.info(
            "orchestrator_decision",
            total_decisions=len(output.decisions),
            actionable=len(actionable),
            outlook=output.market_outlook,
        )

        return {
            "decisions": [d.model_dump() for d in actionable],
            "all_decisions": [d.model_dump() for d in output.decisions],
            "market_outlook": output.market_outlook,
            "summary": output.summary,
        }

    def _build_prompt(
        self,
        tech: dict,
        fund: dict,
        news: dict,
        positions: list,
        portfolio: dict,
        settings,
        backtest: dict | None = None,
        strategy_signals: dict | None = None,
        learning: dict | None = None,
    ) -> str:
        sections = [
            f"## Tracked Pairs: {', '.join(settings.crypto.pair_list)}",
            f"## Capital: ${settings.crypto.max_capital:,.0f}",
            f"## Max Position: {settings.crypto.max_position_pct}% | Max Exposure: {settings.crypto.max_total_exposure_pct}%",
        ]

        if portfolio:
            sections.append(
                f"## Portfolio State\n"
                f"NAV: ${portfolio.get('nav', 0):,.2f} | Cash: ${portfolio.get('cash', 0):,.2f} | "
                f"Exposure: {portfolio.get('total_exposure_pct', 0):.1f}% | "
                f"Drawdown: {portfolio.get('drawdown_pct', 0):.1f}%"
            )

        if positions:
            pos_lines = ["## Current Positions (YOU HOLD THESE — only SELL what you hold)"]
            for p in positions:
                pair_name = p.get('pair', p.get('symbol', '?'))
                entry = float(p.get('avg_entry_price', 0))
                current = float(p.get('current_price', 0))
                unrealized = float(p.get('unrealized_pnl', p.get('unrealized_pl', 0)))
                pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                mv = float(p.get('market_value_usd', p.get('market_value', 0)))
                pos_lines.append(
                    f"- {pair_name}: qty={p.get('qty', 0)}, "
                    f"entry=${entry:,.2f}, current=${current:,.2f}, "
                    f"PnL=${unrealized:+,.2f} ({pnl_pct:+.1f}%), "
                    f"market_value=${mv:,.2f}"
                )
            sections.append("\n".join(pos_lines))
        else:
            sections.append("## Current Positions\nNONE — all cash. Look for BUY opportunities.")

        if tech:
            tech_lines = ["## Technical Signals"]
            for pair, data in tech.items():
                tech_lines.append(
                    f"- {pair}: {data.get('signal', 'N/A')} (score={data.get('score', 0):.2f}, "
                    f"conf={data.get('confidence', 0):.2f}) — {data.get('details', '')}"
                )
            sections.append("\n".join(tech_lines))

        if fund:
            fund_lines = ["## Fundamental Signals"]
            for pair, data in fund.items():
                fund_lines.append(
                    f"- {pair}: {data.get('signal', 'N/A')} (score={data.get('score', 0):.2f}) — "
                    f"{data.get('details', '')}"
                )
            sections.append("\n".join(fund_lines))

        if news:
            news_lines = ["## News Sentiment"]
            if isinstance(news, dict):
                news_lines.append(
                    f"Overall: {news.get('overall_sentiment', 'N/A')} "
                    f"(score={news.get('overall_score', 0):.2f})"
                )
                for evt in news.get("key_events", [])[:5]:
                    news_lines.append(f"- {evt}")
            sections.append("\n".join(news_lines))

        if strategy_signals:
            strat_lines = ["## Strategy Signals (backtested aggressive strategies)"]
            for pair, strats in strategy_signals.items():
                buys = [f"{s['name']}({s['score']:.2f})" for s in strats if s.get("signal") == "buy"]
                sells = [f"{s['name']}({s['score']:.2f})" for s in strats if s.get("signal") == "sell"]
                if buys or sells:
                    parts = []
                    if buys:
                        parts.append(f"BUY: {', '.join(buys)}")
                    if sells:
                        parts.append(f"SELL: {', '.join(sells)}")
                    strat_lines.append(f"- {pair}: {' | '.join(parts)}")
            if len(strat_lines) > 1:
                sections.append("\n".join(strat_lines))

        if backtest and backtest.get("strategy_weights"):
            bt_lines = ["## Backtest Results (3-day walk-forward)"]
            for agg in backtest.get("aggregate", []):
                bt_lines.append(
                    f"- {agg['name']}: Sharpe={agg['sharpe']:.2f}, "
                    f"WR={agg['win_rate']:.0%}, PnL={agg['total_pnl_pct']:.1f}%, "
                    f"weight={agg['weight']:.0%}"
                )
            bt_lines.append(
                "⚡ Favor strategies with higher weight — they performed best in backtest."
            )
            sections.append("\n".join(bt_lines))

        if learning and learning.get("total_trades", 0) > 0:
            lr_lines = [
                "## Live Learning (recent trades)",
                f"- Last {learning['total_trades']} trades: "
                f"WR={learning.get('win_rate', 0):.0%}, PnL={learning.get('total_pnl_pct', 0):+.1f}%",
                f"- Best strategy (live): {learning.get('best_strategy', '?')}",
            ]
            rankings = learning.get("strategy_rankings", {})
            if rankings:
                lr_lines.append(f"- Live scores: {', '.join(f'{k}={v:.2f}' for k, v in rankings.items())}")
            sections.append("\n".join(lr_lines))

        sections.append(
            f"\nMake BUY/SELL/HOLD decisions for each pair.\n"
            f"Confidence threshold: {self._confidence_threshold}.\n"
            f"BUY: any pair where signals are net positive and you have no/small position.\n"
            f"SELL: any HELD pair where signals turned negative, PnL exceeds stop/target, "
            f"or 2+ signal sources are bearish. Be decisive about protecting capital.\n"
            f"HOLD: only when no strong signal in either direction.\n"
            f"Weight confidence using strategy signals and backtest results. "
            f"If multiple backtested strategies agree on BUY, boost confidence. "
            f"DO NOT default to HOLD — deploy or protect aggressively."
        )

        return "\n\n".join(sections)

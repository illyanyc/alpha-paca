"""Alpha-Paca Crypto — main async event loop, scheduler, and rich terminal UI."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
import uvicorn
from rich.console import Console
from rich.live import Live
from sqlalchemy import select

from agents.base import set_healer, set_state_ref
from agents.fundamental_analyst import FundamentalAnalystAgent
from agents.healer import HealerAgent
from agents.news_scout import NewsScoutAgent
from agents.orchestrator import OrchestratorAgent
from agents.order_executor import OrderExecutorAgent
from agents.risk_validator import RiskValidatorAgent
from agents.technical_analyst import TechnicalAnalystAgent
from config import get_settings
from db.engine import Base, async_session_factory, engine
from db.models import CryptoPortfolioState, CryptoPosition, CryptoTrade
from display import build_full_display
from engine.position_sizer import compute_position_size
from services.alpaca_crypto import AlpacaCryptoService
from services.price_tracker import PriceTracker
from services.telegram import TelegramService
from web import app as web_app, init_web

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger("crypto.main")

HEARTBEAT_KEY = "crypto:heartbeat"
HEARTBEAT_INTERVAL = 5
TICK_30S = 30
TICK_5M = 300
TICK_1H = 3600
TICK_24H = 86400

_shutdown = asyncio.Event()
_start_time = time.time()

# ── Shared mutable state for display ─────────────────────────────────
_state = {
    "prices": {},
    "price_history": {},
    "portfolio": {},
    "positions": [],
    "tech_signals": {},
    "fund_signals": {},
    "news_data": {},
    "recent_trades": [],
    "agent_statuses": {
        "news_scout": "idle",
        "technical_analyst": "idle",
        "fundamental_analyst": "idle",
        "orchestrator": "idle",
        "risk_validator": "idle",
        "order_executor": "idle",
    },
    "healing_events": [],
}


def _handle_signal(signum, frame):
    _shutdown.set()


async def create_tables() -> None:
    from db import models as _m  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_portfolio_state(alpaca: AlpacaCryptoService) -> dict:
    settings = get_settings()
    try:
        acct = alpaca.get_account()
        positions = alpaca.get_positions()
    except Exception:
        return {
            "nav": settings.crypto.max_capital,
            "cash": settings.crypto.max_capital,
            "total_exposure_pct": 0,
            "unrealized_pnl": 0,
            "drawdown_pct": 0,
            "positions_count": 0,
        }

    total_mv = sum(float(p.get("market_value", 0)) for p in positions)
    nav = min(float(acct.get("portfolio_value", settings.crypto.max_capital)), settings.crypto.max_capital)
    exposure = (total_mv / nav * 100) if nav > 0 else 0

    return {
        "nav": nav,
        "cash": float(acct.get("cash", 0)),
        "total_exposure_pct": exposure,
        "unrealized_pnl": sum(float(p.get("unrealized_pl", 0)) for p in positions),
        "drawdown_pct": 0,
        "positions_count": len(positions),
    }


async def heartbeat_loop(redis_conn: aioredis.Redis) -> None:
    while not _shutdown.is_set():
        await redis_conn.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=30)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def tick_30s(
    tech_agent: TechnicalAnalystAgent,
    price_tracker: PriceTracker,
    alpaca: AlpacaCryptoService,
) -> None:
    while not _shutdown.is_set():
        try:
            prices = await price_tracker.fetch_and_cache()
            _state["prices"] = prices
            for pair, data in prices.items():
                hist = _state["price_history"].setdefault(pair, [])
                hist.append(data.get("mid", 0))
                if len(hist) > 60:
                    _state["price_history"][pair] = hist[-60:]

            result = await tech_agent.safe_run()
            if isinstance(result, dict) and "error" not in result:
                _state["tech_signals"] = result

            portfolio = await get_portfolio_state(alpaca)
            _state["portfolio"] = portfolio
            _state["positions"] = alpaca.get_positions()
        except Exception:
            logger.exception("tick_30s_error")
        await asyncio.sleep(TICK_30S)


async def tick_5m(
    news_agent: NewsScoutAgent,
    fund_agent: FundamentalAnalystAgent,
    orchestrator: OrchestratorAgent,
    risk_agent: RiskValidatorAgent,
    executor: OrderExecutorAgent,
    price_tracker: PriceTracker,
    alpaca: AlpacaCryptoService,
) -> None:
    await asyncio.sleep(10)
    while not _shutdown.is_set():
        try:
            news_result, fund_result = await asyncio.gather(
                news_agent.safe_run(),
                fund_agent.safe_run(),
            )

            if isinstance(news_result, dict) and "error" not in news_result:
                _state["news_data"] = news_result

            if isinstance(fund_result, dict) and "error" not in fund_result:
                _state["fund_signals"] = fund_result

            positions = alpaca.get_positions()
            portfolio = await get_portfolio_state(alpaca)
            _state["positions"] = positions
            _state["portfolio"] = portfolio

            orch_result = await orchestrator.safe_run(
                positions=positions,
                portfolio_state=portfolio,
            )

            decisions = orch_result.get("decisions", [])
            for decision in decisions:
                risk_result = await risk_agent.safe_run(
                    decision=decision,
                    positions=positions,
                    portfolio_state=portfolio,
                )

                if not risk_result.get("approved", False):
                    continue

                prices = await price_tracker.get_all_cached_prices()
                pair = decision.get("pair", "")
                mid_price = prices.get(pair, {}).get("mid", 0)
                if mid_price <= 0:
                    continue

                exec_result = await executor.safe_run(
                    decision=decision,
                    price=mid_price,
                    available_capital=portfolio.get("cash", 0),
                )

                if exec_result.get("status") == "filled":
                    _state["recent_trades"].append({
                        "pair": pair,
                        "side": exec_result.get("side", decision.get("action")),
                        "qty": exec_result.get("qty", 0),
                        "price": exec_result.get("price", mid_price),
                        "pnl": exec_result.get("pnl", 0),
                        "reasoning": decision.get("reasoning", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    })
                    _state["recent_trades"] = _state["recent_trades"][-20:]

        except Exception:
            logger.exception("tick_5m_error")
        await asyncio.sleep(TICK_5M)


async def tick_1h(telegram: TelegramService, alpaca: AlpacaCryptoService) -> None:
    await asyncio.sleep(60)
    while not _shutdown.is_set():
        try:
            positions = alpaca.get_positions()
            portfolio = await get_portfolio_state(alpaca)
            pos_data = [
                {
                    "pair": p.get("symbol", ""),
                    "qty": p.get("qty", 0),
                    "current_price": p.get("current_price", 0),
                    "unrealized_pnl": p.get("unrealized_pl", 0),
                }
                for p in positions
            ]
            await telegram.hourly_summary(
                positions=pos_data,
                unrealized_pnl=portfolio.get("unrealized_pnl", 0),
                exposure_pct=portfolio.get("total_exposure_pct", 0),
            )
        except Exception:
            logger.exception("tick_1h_error")
        await asyncio.sleep(TICK_1H)


async def tick_24h(telegram: TelegramService) -> None:
    await asyncio.sleep(120)
    while not _shutdown.is_set():
        try:
            async with async_session_factory() as session:
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                stmt = select(CryptoTrade).where(
                    CryptoTrade.status == "closed",
                    CryptoTrade.closed_at >= today,
                )
                result = await session.execute(stmt)
                trades = result.scalars().all()

            if trades:
                total_pnl = sum(float(t.pnl or 0) for t in trades)
                winners = [t for t in trades if (t.pnl or 0) > 0]
                win_rate = len(winners) / len(trades) if trades else 0
                pnls = [(float(t.pnl or 0), t.pair) for t in trades]
                best = max(pnls, key=lambda x: x[0])
                worst = min(pnls, key=lambda x: x[0])
                await telegram.daily_report(
                    total_pnl=total_pnl, win_rate=win_rate, trade_count=len(trades),
                    best_trade=f"{best[1]} +${best[0]:,.2f}",
                    worst_trade=f"{worst[1]} ${worst[0]:+,.2f}",
                )
            else:
                await telegram.send("📈 *Daily Report*: No closed trades today.")
        except Exception:
            logger.exception("tick_24h_error")
        await asyncio.sleep(TICK_24H)


async def display_loop(live: Live, settings) -> None:
    """Update the rich terminal UI every 2 seconds."""
    mode = "PAPER" if settings.alpaca.paper else "LIVE"
    while not _shutdown.is_set():
        try:
            uptime = int(time.time() - _start_time)
            display = build_full_display(
                prices=_state["prices"],
                portfolio=_state["portfolio"],
                positions=_state["positions"],
                tech_signals=_state["tech_signals"],
                fund_signals=_state["fund_signals"],
                news_data=_state["news_data"],
                recent_trades=_state["recent_trades"],
                agent_statuses=_state["agent_statuses"],
                price_history=_state["price_history"],
                healing_events=_state.get("healing_events", []),
                mode=mode,
                uptime_sec=uptime,
            )
            live.update(display)
        except Exception:
            pass
        await asyncio.sleep(2)


async def run() -> None:
    settings = get_settings()
    console = Console()

    await create_tables()

    redis_conn = aioredis.from_url(settings.database.redis_url, decode_responses=True)

    alpaca = AlpacaCryptoService()
    telegram = TelegramService()
    price_tracker = PriceTracker(alpaca)

    healer = HealerAgent()
    set_healer(healer)
    set_state_ref(_state)

    news_agent = NewsScoutAgent()
    tech_agent = TechnicalAnalystAgent(alpaca)
    fund_agent = FundamentalAnalystAgent(alpaca)
    orchestrator = OrchestratorAgent()
    risk_agent = RiskValidatorAgent()
    executor = OrderExecutorAgent(alpaca, telegram)

    _state["portfolio"] = {
        "nav": settings.crypto.max_capital,
        "cash": settings.crypto.max_capital,
        "total_exposure_pct": 0,
        "unrealized_pnl": 0,
        "drawdown_pct": 0,
    }

    await telegram.send(
        "🚀 *Alpha-Paca Crypto Started*\n"
        f"Pairs: {', '.join(settings.crypto.pair_list)}\n"
        f"Capital: ${settings.crypto.max_capital:,.0f}\n"
        f"Mode: {'PAPER' if settings.alpaca.paper else 'LIVE'}"
    )

    # Bind shared state to the web dashboard
    init_web(_state, _start_time, settings)

    # Start the web server alongside the trading loop
    web_port = int(os.environ.get("PORT", "8080"))
    web_config = uvicorn.Config(
        web_app, host="0.0.0.0", port=web_port,
        log_level="warning", access_log=False,
    )
    web_server = uvicorn.Server(web_config)

    is_tty = sys.stdout.isatty()

    if is_tty:
        live_ctx = Live(console=console, refresh_per_second=1, screen=True)
    else:
        live_ctx = None

    async def _safe_web_serve():
        try:
            await web_server.serve()
        except SystemExit:
            logger.warning("web_server_failed", port=web_port)
        except Exception:
            logger.exception("web_server_error")

    async def _run_tasks():
        core_tasks = [
            asyncio.create_task(heartbeat_loop(redis_conn), name="heartbeat"),
            asyncio.create_task(tick_30s(tech_agent, price_tracker, alpaca), name="tick_30s"),
            asyncio.create_task(tick_5m(
                news_agent, fund_agent, orchestrator, risk_agent, executor, price_tracker, alpaca
            ), name="tick_5m"),
            asyncio.create_task(tick_1h(telegram, alpaca), name="tick_1h"),
            asyncio.create_task(tick_24h(telegram), name="tick_24h"),
            asyncio.create_task(_safe_web_serve(), name="web"),
        ]
        if is_tty and live_ctx:
            core_tasks.append(asyncio.create_task(display_loop(live_ctx, settings), name="display"))

        await _shutdown.wait()
        web_server.should_exit = True
        for t in core_tasks:
            t.cancel()
        await asyncio.gather(*core_tasks, return_exceptions=True)

    if live_ctx:
        with live_ctx:
            await _run_tasks()
    else:
        logger.info("web_dashboard_available", url=f"http://0.0.0.0:{web_port}")
        await _run_tasks()

    await price_tracker.close()
    await redis_conn.aclose()
    await engine.dispose()
    await telegram.send("🛑 *Alpha-Paca Crypto Stopped*")


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(run())


if __name__ == "__main__":
    main()

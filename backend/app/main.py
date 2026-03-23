"""AlphaPaca FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown lifecycle."""
    hc = None
    trading_scheduler = None

    try:
        from app.services.hot_config import HotConfig

        hc = HotConfig(
            db_url=_settings.database.database_url,
            redis_url=_settings.database.redis_url,
        )
        await hc.seed_defaults()
        await hc.load()
        application.state.hot_config = hc
        logger.info("hot_config_loaded")
    except Exception as exc:
        logger.warning("hot_config_startup_skip reason=%r", str(exc)[:120])
        application.state.hot_config = None

    try:
        from app.services.alpaca_client import AlpacaService
        from app.tasks.orchestrator import Orchestrator
        from app.tasks.scheduler import TradingScheduler

        alpaca = AlpacaService()
        orchestrator = Orchestrator(alpaca=alpaca, hot_config=hc)
        trading_scheduler = TradingScheduler(
            orchestrator=orchestrator,
            alpaca=alpaca,
            hot_config=hc,
        )
        trading_scheduler.start()
        application.state.orchestrator = orchestrator
        application.state.trading_scheduler = trading_scheduler
        logger.info("trading_loop_started")
    except Exception as exc:
        logger.warning("trading_loop_startup_skip reason=%r", str(exc)[:120])

    yield

    if trading_scheduler is not None:
        trading_scheduler.stop()


app = FastAPI(
    title="AlphaPaca Trading API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount routers ─────────────────────────────────────────────────────────
from app.routers import (
    auth,
    backtest,
    health,
    pods,
    portfolio,
    positions,
    regime,
    risk,
    settings,
    signals,
    trades,
    ws,
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(pods.router, prefix="/api/pods", tags=["pods"])
app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
app.include_router(risk.router, prefix="/api/risk", tags=["risk"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(ws.router, tags=["websocket"])
app.include_router(regime.router, prefix="/api/regime", tags=["regime"])
app.include_router(health.router, prefix="/api/health", tags=["health"])


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

"""APScheduler-based trading loop — pre-market, intraday, and post-market jobs."""

from __future__ import annotations

from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.alpaca_client import AlpacaService
from app.services.market_calendar import MarketCalendar
from app.services.universe_builder import UniverseBuilder
from app.tasks.orchestrator import Orchestrator

logger = structlog.get_logger(__name__)


class TradingScheduler:
    """Manages the scheduled trading loop aligned to US equity market hours."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        alpaca: AlpacaService,
        hot_config: Any | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._alpaca = alpaca
        self._calendar = MarketCalendar(alpaca)
        self._universe_builder = UniverseBuilder()
        self._hot_config = hot_config
        self._scheduler = AsyncIOScheduler(timezone="US/Eastern")
        self._universe: list[str] = []

    def start(self) -> None:
        self._scheduler.add_job(
            self._run_pre_market,
            CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="US/Eastern"),
            id="pre_market",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_intraday,
            CronTrigger(hour="9-15", minute="*/5", day_of_week="mon-fri", timezone="US/Eastern"),
            id="intraday",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_post_market,
            CronTrigger(hour=16, minute=5, day_of_week="mon-fri", timezone="US/Eastern"),
            id="post_market",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("trading_scheduler_started")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("trading_scheduler_stopped")

    async def _run_pre_market(self) -> None:
        if not self._is_trading_day():
            logger.info("skipping_pre_market_not_trading_day")
            return
        try:
            universe_data = await self._universe_builder.build_universe()
            self._universe = [row.get("symbol", "") for row in universe_data if row.get("symbol")]
            logger.info("universe_built", size=len(self._universe))
            result = self._orchestrator.run_pre_market(self._universe)
            logger.info("pre_market_complete", scan_results=len(result.get("scan_results", {})))
        except Exception:
            logger.exception("pre_market_failed")

    async def _run_intraday(self) -> None:
        if not self._is_market_open():
            return
        try:
            result = self._orchestrator.run_intraday({})
            logger.info(
                "intraday_cycle_complete",
                submitted=len(result.get("submitted", [])),
                rejected=len(result.get("rejected", [])),
            )
        except Exception:
            logger.exception("intraday_failed")

    async def _run_post_market(self) -> None:
        if not self._is_trading_day():
            return
        try:
            result = self._orchestrator.run_post_market()
            logger.info("post_market_complete", positions=result.get("positions", 0))
        except Exception:
            logger.exception("post_market_failed")

    def _is_trading_day(self) -> bool:
        try:
            from datetime import date

            return self._calendar.is_trading_day(date.today())
        except Exception:
            logger.warning("calendar_check_failed_assuming_trading_day")
            return True

    def _is_market_open(self) -> bool:
        try:
            return self._calendar.is_market_open()
        except Exception:
            return False

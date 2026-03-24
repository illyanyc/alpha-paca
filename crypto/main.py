"""Alpha-Paca Crypto — Adaptive Momentum + News Alpha trading system."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

import redis.asyncio as aioredis
import structlog
import uvicorn
from rich.console import Console
from rich.live import Live
import sqlalchemy
from sqlalchemy import select

from agents.base import set_healer, set_state_ref
from agents.healer import HealerAgent
from agents.momentum_trader import MomentumTraderAgent
from agents.news_scout import NewsScoutAgent
from agents.order_executor import OrderExecutorAgent
from agents.risk_guard import RiskGuard
from agents.swing_sniper import SwingSniperAgent
from config import get_settings
from db.engine import Base, async_session_factory, engine
from db.models import CryptoPortfolioState, CryptoPosition, CryptoTrade
from display import build_full_display
from engine.exit_manager import ExitManager
from engine.indicators import compute_all
from engine.leverage_sizer import compute_position_size, compute_leverage_size, get_loss_tracker
from engine.regime import detect_regime
from engine.trade_journal import log_decision
from services.coinbase_crypto import CoinbaseCryptoService
from services.onchain_client import fetch_all_onchain
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

_shutdown = asyncio.Event()
_start_time = time.time()

_state: dict[str, Any] = {
    "prices": {},
    "price_history": {},
    "portfolio": {},
    "positions": [],
    "tech_signals": {},
    "news_data": {},
    "recent_trades": [],
    "agent_statuses": {
        "momentum": "idle",
        "swing_sniper": "idle",
        "order_executor": "idle",
        "news_scout": "idle",
    },
    "healing_events": [],
    "agent_log": [],
    "exchange_status": "checking",
    "exchange_error": "",
    "trading_mode": "",
    "pnl_summary": {
        "total_realized_pnl": 0.0,
        "total_trades": 0,
        "total_win_rate": 0.0,
        "daily_realized_pnl": 0.0,
        "daily_trades": 0,
        "daily_win_rate": 0.0,
        "per_pair": {},
    },
    "regime": {},
    "onchain": {},
    "indicators_5m": {},
    "indicators_4h": {},
    "indicators_daily": {},
    "composite_scores": {},
    "candles_5m": {},
    "candles_1m": {},
    "candles_4h": {},
    "candles_daily": {},
}

_exchange_ref: CoinbaseCryptoService | None = None
_risk_guard: RiskGuard | None = None
_exit_manager: ExitManager | None = None
_momentum_bot: MomentumTraderAgent | None = None
_news_agent: NewsScoutAgent | None = None
_executor: OrderExecutorAgent | None = None


def get_exchange() -> CoinbaseCryptoService | None:
    return _exchange_ref


async def reload_coinbase_keys(api_key: str, api_secret: str) -> dict[str, str]:
    """Hot-swap Coinbase credentials, persist to Redis, update in-memory client."""
    global _exchange_ref
    from services.settings_store import save_coinbase_keys
    from services.coinbase_crypto import _is_pem_key

    if not _is_pem_key(api_secret):
        err = (
            "Your API secret is not a PEM private key. "
            "Create CDP keys at https://portal.cdp.coinbase.com/projects/api-keys "
            "using ECDSA (ES256)."
        )
        _state["exchange_status"] = "unauthorized"
        _state["exchange_error"] = err
        return {"status": "unauthorized", "error": err}

    try:
        from coinbase.rest import RESTClient
        test_client = RESTClient(api_key=api_key, api_secret=api_secret.replace("\\n", "\n").strip())
        test_client.get_accounts(limit=1)
        if _exchange_ref:
            _exchange_ref.replace_client(api_key, api_secret)
        _state["exchange_status"] = "connected"
        _state["exchange_error"] = ""
        _state["trading_mode"] = "LIVE"
        await save_coinbase_keys(api_key, api_secret)
        logger.info("coinbase_keys_reloaded")
        return {"status": "connected"}
    except Exception as e:
        err = str(e).strip()
        _state["exchange_status"] = "unauthorized"
        _state["exchange_error"] = err
        return {"status": "unauthorized", "error": err}


async def update_trading_settings(new_settings: dict) -> dict[str, str]:
    """Update trading parameters in-memory and persist to Redis."""
    from services.settings_store import save_trading_settings

    settings = get_settings()
    updated = []
    allowed = {
        "max_capital", "pairs", "max_risk_per_trade_pct", "max_leverage",
        "min_conviction", "daily_loss_halt_pct", "max_drawdown_pct",
        "max_concurrent_per_bot", "max_concurrent_total",
        "composite_buy_threshold", "composite_exit_threshold",
        "atr_stop_multiplier", "atr_tp_multiplier",
        "macd_fast", "macd_slow", "macd_signal", "rsi_period",
        "ema_fast", "ema_slow",
        "trading_hours_start", "trading_hours_end", "primary_window_end",
        "momentum_eval_interval_sec", "news_poll_interval_sec", "article_scrape_interval_sec",
        "day_min_rr_ratio", "day_min_trade_interval_sec", "day_max_hold_hours",
        "day_eval_interval_sec", "swing_min_rr_ratio", "swing_min_trade_interval_sec",
        "swing_eval_interval_sec", "cooldown_after_losses", "cooldown_halt_after_losses",
    }
    for k, v in new_settings.items():
        if k in allowed and hasattr(settings.crypto, k):
            setattr(settings.crypto, k, type(getattr(settings.crypto, k))(v))
            updated.append(k)

    persisted = {k: getattr(settings.crypto, k) for k in allowed if hasattr(settings.crypto, k)}
    await save_trading_settings(persisted)
    logger.info("trading_settings_updated", updated=updated)
    return {"status": "ok", "updated": updated}


def _handle_signal(signum, frame):
    _shutdown.set()


async def create_tables() -> None:
    from db import models as _m  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        for stmt in [
            "ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS side VARCHAR(10) NOT NULL DEFAULT 'long'",
            "ALTER TABLE crypto_trades ADD COLUMN IF NOT EXISTS exchange_order_id VARCHAR(64)",
            "ALTER TABLE crypto_trades ADD COLUMN IF NOT EXISTS bot_id VARCHAR(10) NOT NULL DEFAULT 'swing'",
            "ALTER TABLE crypto_trades ADD COLUMN IF NOT EXISTS target_price FLOAT",
            "ALTER TABLE crypto_trades ADD COLUMN IF NOT EXISTS stop_price FLOAT",
            "ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS bot_id VARCHAR(10) NOT NULL DEFAULT 'swing'",
        ]:
            await conn.execute(sqlalchemy.text(stmt))
        try:
            await conn.execute(sqlalchemy.text(
                "ALTER TABLE crypto_positions DROP CONSTRAINT IF EXISTS crypto_positions_pair_key"
            ))
        except Exception:
            pass


async def enrich_positions(exchange_positions: list[dict]) -> list[dict]:
    """Cross-reference exchange holdings with DB entry prices for accurate PnL."""
    async with async_session_factory() as session:
        stmt = select(CryptoPosition)
        result = await session.execute(stmt)
        db_positions = list(result.scalars().all())

    db_by_pair_bot: dict[str, CryptoPosition] = {}
    db_by_pair: dict[str, list[CryptoPosition]] = {}
    for p in db_positions:
        db_by_pair_bot[f"{p.pair}:{p.bot_id}"] = p
        db_by_pair.setdefault(p.pair, []).append(p)

    enriched = []
    seen_pairs = set()

    for ep in exchange_positions:
        pair = ep.get("symbol", ep.get("pair", ""))
        current_price = float(ep.get("current_price", 0))
        qty = float(ep.get("qty", 0))
        seen_pairs.add(pair)

        db_list = db_by_pair.get(pair, [])
        if db_list:
            for db_pos in db_list:
                entry_price = float(db_pos.avg_entry_price) if db_pos.avg_entry_price > 0 else current_price
                pos_qty = float(db_pos.qty)
                if pos_qty <= 0:
                    continue
                unrealized_pnl = (current_price - entry_price) * pos_qty
                unrealized_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                market_value = pos_qty * current_price
                enriched.append({
                    **ep,
                    "pair": pair,
                    "bot_id": db_pos.bot_id,
                    "side": db_pos.side or "long",
                    "avg_entry_price": entry_price,
                    "current_price": current_price,
                    "qty": pos_qty,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "market_value": market_value,
                    "market_value_usd": market_value,
                })
        else:
            unrealized_pnl = 0
            market_value = qty * current_price
            enriched.append({
                **ep,
                "pair": pair,
                "bot_id": "swing",
                "side": "long",
                "avg_entry_price": current_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pl": unrealized_pnl,
                "unrealized_pnl_pct": 0,
                "market_value": market_value,
                "market_value_usd": market_value,
            })

    return enriched


async def sync_positions_to_db(enriched_positions: list[dict]) -> None:
    """Keep DB CryptoPosition rows in sync with actual exchange state."""
    from decimal import Decimal as D

    async with async_session_factory() as session:
        stmt = select(CryptoPosition)
        result = await session.execute(stmt)
        db_all = list(result.scalars().all())

        for pos in db_all:
            matched = False
            for ep in enriched_positions:
                if ep.get("pair") == pos.pair and ep.get("bot_id") == pos.bot_id:
                    pos.current_price = D(str(ep.get("current_price", 0)))
                    pos.market_value_usd = D(str(ep.get("market_value_usd", 0)))
                    pos.unrealized_pnl = D(str(ep.get("unrealized_pnl", 0)))
                    matched = True
                    break
            if not matched and pos.side != "short":
                pos.qty = D(0)
                pos.current_price = D(0)
                pos.market_value_usd = D(0)
                pos.unrealized_pnl = D(0)

        await session.commit()


_high_water_mark: float = 0.0


async def get_portfolio_state(exchange: CoinbaseCryptoService) -> dict:
    """Fetch real Coinbase account equity and positions."""
    global _high_water_mark
    try:
        acct = await asyncio.to_thread(exchange.get_account)
        raw_positions = await asyncio.to_thread(exchange.get_positions)
        positions = await enrich_positions(raw_positions)
    except Exception:
        prev = _state.get("portfolio", {})
        return {
            "nav": prev.get("nav", 0),
            "cash": prev.get("cash", 0),
            "total_exposure_pct": prev.get("total_exposure_pct", 0),
            "unrealized_pnl": prev.get("unrealized_pnl", 0),
            "drawdown_pct": prev.get("drawdown_pct", 0),
            "positions_count": prev.get("positions_count", 0),
        }

    total_mv = sum(float(p.get("market_value_usd", 0)) for p in positions)
    nav = float(acct.get("portfolio_value", 0)) or float(acct.get("equity", 0))
    cash = float(acct.get("cash", 0))
    exposure = (total_mv / nav * 100) if nav > 0 else 0
    unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions)

    if nav > _high_water_mark:
        _high_water_mark = nav
    drawdown_pct = ((_high_water_mark - nav) / _high_water_mark * 100) if _high_water_mark > 0 else 0

    pnl_s = _state.get("pnl_summary", {})
    return {
        "nav": nav,
        "cash": cash,
        "total_exposure_pct": round(exposure, 1),
        "unrealized_pnl": unrealized,
        "drawdown_pct": drawdown_pct,
        "positions_count": len(positions),
        "realized_pnl_today": pnl_s.get("daily_realized_pnl", 0),
        "total_realized_pnl": pnl_s.get("total_realized_pnl", 0),
        "total_trades": pnl_s.get("total_trades", 0),
        "total_win_rate": pnl_s.get("total_win_rate", 0),
        "daily_trades": pnl_s.get("daily_trades", 0),
        "daily_win_rate": pnl_s.get("daily_win_rate", 0),
    }


# ── Tick loops ────────────────────────────────────────────────────────


async def heartbeat_loop(redis_conn: aioredis.Redis) -> None:
    while not _shutdown.is_set():
        await redis_conn.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=120)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def tick_15s(
    price_tracker: PriceTracker,
    exchange: CoinbaseCryptoService,
) -> None:
    """Prices, 5m/4H indicators, regime detection every 15 seconds."""
    while not _shutdown.is_set():
        try:
            prices = await price_tracker.fetch_and_cache()
            _state["prices"] = prices
            for pair, data in prices.items():
                hist = _state["price_history"].setdefault(pair, [])
                hist.append(data.get("mid", 0))
                if len(hist) > 60:
                    _state["price_history"][pair] = hist[-60:]

            settings = get_settings()

            # 5m indicators for DaySniper
            for pair in settings.crypto.pair_list:
                try:
                    bars_5m = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="FIVE_MINUTE", lookback_minutes=600,
                    )
                    if len(bars_5m) >= 30:
                        ind = compute_all(bars_5m)
                        _state["indicators_5m"][pair] = ind
                        _state["candles_5m"][pair] = bars_5m[-120:]
                except Exception as e:
                    logger.warning("bars_5m_fetch_error", pair=pair, error=str(e))

                try:
                    bars_1m = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="ONE_MINUTE", lookback_minutes=120,
                    )
                    if bars_1m:
                        _state["candles_1m"][pair] = bars_1m[-120:]
                except Exception as e:
                    logger.warning("bars_1m_fetch_error", pair=pair, error=str(e))

            # 4H indicators for SwingSniper (computed less often but refreshed here)
            for pair in settings.crypto.pair_list:
                try:
                    bars_4h = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="FOUR_HOUR", lookback_minutes=30 * 24 * 60,
                    )
                    if len(bars_4h) >= 30:
                        ind = compute_all(bars_4h)
                        _state["indicators_4h"][pair] = ind
                        _state["candles_4h"][pair] = bars_4h[-120:]
                except Exception as e:
                    logger.warning("bars_4h_fetch_error", pair=pair, error=str(e))

            # Daily candles for the MACD regime filter
            for pair in settings.crypto.pair_list:
                try:
                    bars_daily = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="ONE_DAY", lookback_minutes=250 * 24 * 60,
                    )
                    if len(bars_daily) >= 30:
                        ind = compute_all(bars_daily)
                        _state["indicators_daily"][pair] = ind
                        _state["candles_daily"][pair] = bars_daily[-250:]
                except Exception as e:
                    logger.warning("bars_daily_fetch_error", pair=pair, error=str(e))

            # Regime detection on hourly candles
            try:
                first_pair = settings.crypto.pair_list[0]
                hourly_bars = await asyncio.to_thread(
                    exchange.get_bars, first_pair, granularity="ONE_HOUR", lookback_minutes=168 * 60,
                )
                if len(hourly_bars) >= 48:
                    hourly_closes = [b["close"] for b in hourly_bars]
                    regime_state = detect_regime(hourly_closes)
                    regime_dict = {
                        "regime": regime_state.regime.value,
                        "confidence": regime_state.confidence,
                        "label": regime_state.label,
                        "features": regime_state.features,
                    }
                    _state["regime"] = regime_dict
            except Exception as e:
                logger.warning("regime_detection_error", error=str(e))

            # Portfolio + positions
            portfolio = await get_portfolio_state(exchange)
            _state["portfolio"] = portfolio

            nav = portfolio.get("nav", 0)
            if nav > 0:
                curve = _state.setdefault("equity_curve", [])
                curve.append({"ts": datetime.now(timezone.utc).isoformat(), "nav": nav})
                if len(curve) > 2880:
                    _state["equity_curve"] = curve[-2880:]

            try:
                raw_pos = await asyncio.to_thread(exchange.get_positions)
                enriched = await enrich_positions(raw_pos)
                _state["positions"] = enriched
                await sync_positions_to_db(enriched)
            except Exception as e:
                logger.warning("positions_sync_error", error=str(e))

        except Exception as e:
            logger.error("tick_15s_error", error_msg=str(e))
        await asyncio.sleep(15)


async def tick_momentum_trader(
    momentum_bot: MomentumTraderAgent,
    risk_guard: RiskGuard,
    executor: OrderExecutorAgent,
    exit_manager: ExitManager,
    exchange: CoinbaseCryptoService,
) -> None:
    """Adaptive Momentum evaluates entries every 60s using 4H composite scoring."""
    await asyncio.sleep(20)
    settings = get_settings()
    while not _shutdown.is_set():
        try:
            positions = _state.get("positions", [])
            portfolio = _state.get("portfolio", {})

            result = await momentum_bot.safe_run(
                indicators_4h=_state.get("indicators_4h", {}),
                indicators_daily=_state.get("indicators_daily", {}),
                news_data=_state.get("news_data", {}),
                onchain=_state.get("onchain", {}),
                microstructure={},
                positions=positions,
                portfolio=portfolio,
                prices=_state.get("prices", {}),
            )

            all_decisions = result.get("all_decisions", [])
            for d in all_decisions:
                _state["composite_scores"][d.get("pair", "")] = d.get("composite_score", 0)
                price_data = _state.get("prices", {}).get(d.get("pair", ""), {})
                mid_price = price_data.get("mid", 0)
                await log_decision(
                    bot_id="momentum", pair=d["pair"], action=d["action"],
                    conviction=d["conviction"], price_at_decision=mid_price,
                    reasoning=d.get("reasoning", ""),
                    target_price=d.get("target_price"),
                    stop_price=d.get("stop_price"),
                    indicators=_state.get("indicators_4h", {}).get(d["pair"]),
                    regime=_state.get("regime", {}).get("label"),
                    portfolio_state=portfolio,
                    positions=positions,
                )

            decisions = result.get("decisions", [])
            for decision in decisions:
                pair = decision["pair"]
                decision["bot_id"] = "momentum"

                price_data = _state.get("prices", {}).get(pair, {})
                mid_price = price_data.get("mid", 0)
                if mid_price <= 0:
                    continue

                decision["entry_price"] = mid_price

                verdict = risk_guard.check("momentum", decision, positions, portfolio)
                if not verdict.approved:
                    momentum_bot.think(f"[momentum] {pair} REJECTED: {verdict.reason}")
                    continue

                atr = _state.get("indicators_4h", {}).get(pair, {}).get("atr")
                cash = portfolio.get("cash", 0)
                nav = portfolio.get("nav", cash)
                cap = settings.crypto.max_capital
                tradeable = min(nav, cap) if cap > 0 else nav

                if decision["action"] == "BUY":
                    sized = compute_position_size(
                        pair=pair, bot_id="momentum",
                        account_nav=tradeable,
                        entry_price=mid_price,
                        atr_value=atr or mid_price * 0.02,
                    )
                    if not sized:
                        continue
                    notional = sized.notional_usd

                    exit_manager.register_position(
                        pair, "momentum", mid_price,
                        atr_value=atr or mid_price * 0.02,
                        stop_multiplier=settings.crypto.atr_stop_multiplier,
                        tp_multiplier=settings.crypto.atr_tp_multiplier,
                    )
                else:
                    notional = 0

                exec_result = await executor.safe_run(
                    decision=decision, price=mid_price, notional=notional,
                )

                if exec_result.get("status") == "filled":
                    risk_guard.record_trade_time("momentum", pair)
                    pnl = exec_result.get("pnl")
                    if pnl is not None:
                        if pnl > 0:
                            risk_guard.record_win("momentum")
                            get_loss_tracker().record("momentum", pair, True)
                        else:
                            risk_guard.record_loss("momentum")
                            get_loss_tracker().record("momentum", pair, False)
                        if decision["action"] == "SELL":
                            exit_manager.remove_position(pair, "momentum")

                    _state["recent_trades"].append({
                        "pair": pair,
                        "side": exec_result.get("side"),
                        "qty": exec_result.get("qty", 0),
                        "price": exec_result.get("price", mid_price),
                        "pnl": pnl or 0,
                        "bot_id": "momentum",
                        "reasoning": decision.get("reasoning", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    })
                    _state["recent_trades"] = _state["recent_trades"][-30:]

        except Exception as e:
            logger.error("tick_momentum_trader_error", error_msg=str(e))

        try:
            from services.settings_store import save_agent_log as _sal, load_pnl_summary
            await _sal(_state.get("agent_log", []))
            _state["pnl_summary"] = await load_pnl_summary()
        except Exception as e:
            logger.warning("momentum_log_save_error", error=str(e))

        await asyncio.sleep(settings.crypto.momentum_eval_interval_sec)


async def tick_exit_manager(
    exit_manager: ExitManager,
    executor: OrderExecutorAgent,
    exchange: CoinbaseCryptoService,
) -> None:
    """ATR trailing stop / TP / signal / time exits every 15 seconds."""
    await asyncio.sleep(25)
    settings = get_settings()
    while not _shutdown.is_set():
        try:
            positions = _state.get("positions", [])
            indicators_4h = _state.get("indicators_4h", {})
            composite_scores = _state.get("composite_scores", {})

            pending_exits = exit_manager.check_exits(
                positions=positions,
                indicators_4h=indicators_4h,
                composite_scores=composite_scores,
                exit_threshold=settings.crypto.composite_exit_threshold,
            )

            for pe in pending_exits:
                logger.info(
                    "exit_triggered",
                    pair=pe.pair, bot_id=pe.bot_id,
                    exit_type=pe.exit_type, reason=pe.reason,
                )
                exec_result = await executor.safe_run(
                    decision={
                        "action": "SELL", "pair": pe.pair, "confidence": 0.99,
                        "reasoning": f"{pe.exit_type.upper()}: {pe.reason}",
                        "bot_id": pe.bot_id,
                    },
                    price=pe.exit_price, notional=0,
                )
                if exec_result.get("status") == "filled":
                    exit_manager.remove_position(pe.pair, pe.bot_id)
                    pnl = exec_result.get("pnl")
                    if pnl is not None:
                        if pnl > 0:
                            _risk_guard.record_win(pe.bot_id) if _risk_guard else None
                            get_loss_tracker().record(pe.bot_id, pe.pair, True)
                        else:
                            _risk_guard.record_loss(pe.bot_id) if _risk_guard else None
                            get_loss_tracker().record(pe.bot_id, pe.pair, False)

                    _state["recent_trades"].append({
                        "pair": pe.pair,
                        "side": "SELL",
                        "qty": exec_result.get("qty", 0),
                        "price": exec_result.get("price", pe.exit_price),
                        "pnl": pnl or 0,
                        "bot_id": pe.bot_id,
                        "reasoning": pe.reason,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    })
                    _state["recent_trades"] = _state["recent_trades"][-30:]

        except Exception as e:
            logger.error("tick_exit_manager_error", error_msg=str(e))
        await asyncio.sleep(15)


async def tick_swing_sniper(
    swing_bot: SwingSniperAgent,
    risk_guard: RiskGuard,
    executor: OrderExecutorAgent,
    price_tracker: PriceTracker,
    exchange: CoinbaseCryptoService,
) -> None:
    """SwingSniper evaluates entries/exits every 1 hour."""
    await asyncio.sleep(30)
    settings = get_settings()
    while not _shutdown.is_set():
        try:
            positions = _state.get("positions", [])
            portfolio = _state.get("portfolio", {})

            result = await swing_bot.safe_run(
                indicators=_state.get("indicators_4h", {}),
                regime=_state.get("regime", {}),
                news=_state.get("news_data", {}),
                onchain=_state.get("onchain", {}),
                positions=positions,
                portfolio=portfolio,
                candles_4h=_state.get("candles_4h", {}),
                prices=_state.get("prices", {}),
            )

            all_decisions = result.get("all_decisions", [])
            for d in all_decisions:
                price_data = _state.get("prices", {}).get(d.get("pair", ""), {})
                mid_price = price_data.get("mid", 0)
                await log_decision(
                    bot_id="swing", pair=d["pair"], action=d["action"],
                    conviction=d["conviction"], price_at_decision=mid_price,
                    reasoning=d.get("reasoning", ""),
                    target_price=d.get("target_price"),
                    stop_price=d.get("stop_price"),
                    indicators=_state.get("indicators_4h", {}).get(d["pair"]),
                    regime=_state.get("regime", {}).get("label"),
                    portfolio_state=portfolio,
                    positions=positions,
                )

            decisions = result.get("decisions", [])
            for decision in decisions:
                pair = decision["pair"]
                decision["bot_id"] = "swing"

                price_data = _state.get("prices", {}).get(pair, {})
                mid_price = price_data.get("mid", 0)
                if mid_price <= 0:
                    continue

                decision["entry_price"] = mid_price

                verdict = risk_guard.check("swing", decision, positions, portfolio)
                if not verdict.approved:
                    swing_bot.think(f"[swing] {pair} REJECTED: {verdict.reason}")
                    continue

                atr = _state.get("indicators_4h", {}).get(pair, {}).get("atr")
                cash = portfolio.get("cash", 0)
                cap = settings.crypto.max_capital
                tradeable = min(cash, cap) if cap > 0 else cash

                if decision["action"] == "BUY":
                    sized = compute_leverage_size(
                        pair=pair, conviction=decision["conviction"],
                        bot_id="swing", available_capital=tradeable,
                        atr_value=atr, price=mid_price,
                    )
                    if not sized:
                        continue
                    notional = sized.notional_usd
                else:
                    notional = 0

                exec_result = await executor.safe_run(
                    decision=decision, price=mid_price, notional=notional,
                )

                if exec_result.get("status") == "filled":
                    risk_guard.record_trade_time("swing", pair)
                    pnl = exec_result.get("pnl")
                    if pnl is not None:
                        if pnl > 0:
                            risk_guard.record_win("swing")
                            get_loss_tracker().record("swing", pair, True)
                        else:
                            risk_guard.record_loss("swing")
                            get_loss_tracker().record("swing", pair, False)

                    _state["recent_trades"].append({
                        "pair": pair,
                        "side": exec_result.get("side"),
                        "qty": exec_result.get("qty", 0),
                        "price": exec_result.get("price", mid_price),
                        "pnl": pnl or 0,
                        "bot_id": "swing",
                        "reasoning": decision.get("reasoning", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    })
                    _state["recent_trades"] = _state["recent_trades"][-30:]

        except Exception as e:
            logger.error("tick_swing_sniper_error", error_msg=str(e))

        try:
            from services.settings_store import save_agent_log as _sal, load_pnl_summary
            await _sal(_state.get("agent_log", []))
            _state["pnl_summary"] = await load_pnl_summary()
        except Exception as e:
            logger.warning("swing_sniper_log_save_error", error=str(e))

        await asyncio.sleep(settings.crypto.swing_eval_interval_sec)


async def tick_swing_exit_check(
    executor: OrderExecutorAgent,
    exchange: CoinbaseCryptoService,
) -> None:
    """Hard stop/target check for swing positions every 5 minutes."""
    await asyncio.sleep(35)
    while not _shutdown.is_set():
        try:
            positions = _state.get("positions", [])
            for pos in positions:
                if pos.get("bot_id") != "swing":
                    continue
                pair = pos.get("pair", "")
                qty = float(pos.get("qty", 0))
                if qty <= 0 or not pair:
                    continue

                current_price = float(pos.get("current_price", 0))
                entry_price = float(pos.get("avg_entry_price", 0))
                if entry_price <= 0 or current_price <= 0:
                    continue

                pnl_pct = (current_price - entry_price) / entry_price * 100

                # Hard stop for swing: -5%
                if pnl_pct <= -5.0:
                    await executor.safe_run(
                        decision={
                            "action": "SELL", "pair": pair, "confidence": 0.99,
                            "reasoning": f"SWING STOP-LOSS: {pnl_pct:.1f}%",
                            "bot_id": "swing",
                        },
                        price=current_price, notional=0,
                    )

        except Exception as e:
            logger.error("tick_swing_exit_error", error_msg=str(e))
        await asyncio.sleep(300)


async def tick_news_fast(news_agent: NewsScoutAgent) -> None:
    """News polling (every news_poll_interval_sec, default 5min; Tavily hourly)."""
    await asyncio.sleep(10)
    settings = get_settings()
    while not _shutdown.is_set():
        try:
            news_result = await news_agent.safe_run()
            if isinstance(news_result, dict) and "error" not in news_result:
                _state["news_data"] = news_result
        except Exception as e:
            logger.error("tick_news_fast_error", error_msg=str(e))
        await asyncio.sleep(settings.crypto.news_poll_interval_sec)


async def tick_onchain() -> None:
    """On-chain data (funding, OI, F&G, liquidations) every 5 minutes."""
    await asyncio.sleep(15)
    while not _shutdown.is_set():
        try:
            onchain = await fetch_all_onchain()
            if onchain:
                _state["onchain"] = {
                    "fear_greed_index": onchain.fear_greed_index,
                    "fear_greed_label": onchain.fear_greed_label,
                    "btc_funding_rate": onchain.btc_funding_rate,
                    "eth_funding_rate": onchain.eth_funding_rate,
                    "btc_oi_change_pct": onchain.btc_oi_change_pct,
                    "oi_rising": onchain.oi_rising,
                    "long_short_ratio": onchain.long_short_ratio,
                    "exchange_flow_signal": onchain.exchange_flow_signal,
                    "liquidation_cascade": onchain.liquidation_cascade,
                    "liquidation_1h_usd": onchain.liquidation_1h_usd,
                    "signal": onchain.signal,
                    "score": onchain.score,
                }
        except Exception as e:
            logger.error("tick_onchain_error", error_msg=str(e))
        await asyncio.sleep(300)


async def run_rebalance() -> dict[str, Any]:
    """On-demand rebalance: pull fresh data, score all pairs, execute qualifying trades.

    Called from the dashboard Rebalance button. Returns a summary dict.
    """
    exchange = _exchange_ref
    momentum_bot = _momentum_bot
    news_agent = _news_agent
    risk_guard = _risk_guard
    executor = _executor
    exit_mgr = _exit_manager

    if not exchange or not exchange.is_authenticated:
        return {"status": "error", "message": "Exchange not connected"}
    if not momentum_bot or not executor or not risk_guard:
        return {"status": "error", "message": "Trading agents not initialized"}

    settings = get_settings()
    pairs = settings.crypto.pair_list
    results: list[dict[str, Any]] = []

    logger.info("rebalance_started", pairs=len(pairs))

    # 1) Fresh news (force Tavily refresh)
    news_data: dict = {}
    if news_agent:
        try:
            news_agent._last_poll_time = 0
            news_agent._news_client._scrape_last_fetch = 0
            news_data = await news_agent.safe_run()
            if isinstance(news_data, dict) and "error" not in news_data:
                _state["news_data"] = news_data
        except Exception as e:
            logger.warning("rebalance_news_error", error=str(e))

    # 2) Fresh on-chain data
    try:
        from services.onchain_client import OnchainClient
        oc = OnchainClient()
        onchain = await oc.fetch()
        _state["onchain"] = {
            "fear_greed_index": onchain.fear_greed_index,
            "fear_greed_label": onchain.fear_greed_label,
            "btc_funding_rate": onchain.btc_funding_rate,
            "btc_oi_change_pct": onchain.btc_oi_change_pct,
            "oi_rising": onchain.oi_rising,
            "long_short_ratio": onchain.long_short_ratio,
            "exchange_flow_signal": onchain.exchange_flow_signal,
            "liquidation_cascade": onchain.liquidation_cascade,
            "liquidation_1h_usd": onchain.liquidation_1h_usd,
            "signal": onchain.signal,
            "score": onchain.score,
        }
    except Exception as e:
        logger.warning("rebalance_onchain_error", error=str(e))

    # 3) Fresh technicals for all pairs
    for pair in pairs:
        try:
            bars_4h = await asyncio.to_thread(
                exchange.get_bars, pair, granularity="FOUR_HOUR", lookback_minutes=30 * 24 * 60,
            )
            if len(bars_4h) >= 30:
                ind = compute_all(bars_4h)
                _state["indicators_4h"][pair] = ind
                _state["candles_4h"][pair] = bars_4h[-120:]
        except Exception as e:
            logger.warning("rebalance_4h_error", pair=pair, error=str(e))

        try:
            bars_daily = await asyncio.to_thread(
                exchange.get_bars, pair, granularity="ONE_DAY", lookback_minutes=250 * 24 * 60,
            )
            if len(bars_daily) >= 30:
                ind = compute_all(bars_daily)
                _state["indicators_daily"][pair] = ind
        except Exception as e:
            logger.warning("rebalance_daily_error", pair=pair, error=str(e))

    # 4) Fresh prices + portfolio
    try:
        portfolio = await get_portfolio_state(exchange)
        _state["portfolio"] = portfolio
        raw_pos = await asyncio.to_thread(exchange.get_positions)
        enriched = await enrich_positions(raw_pos)
        _state["positions"] = enriched
    except Exception as e:
        logger.warning("rebalance_portfolio_error", error=str(e))

    positions = _state.get("positions", [])
    portfolio = _state.get("portfolio", {})

    # 5) Run momentum evaluation on all pairs
    try:
        result = await momentum_bot.safe_run(
            indicators_4h=_state.get("indicators_4h", {}),
            indicators_daily=_state.get("indicators_daily", {}),
            news_data=_state.get("news_data", {}),
            onchain=_state.get("onchain", {}),
            microstructure={},
            positions=positions,
            portfolio=portfolio,
            prices=_state.get("prices", {}),
        )
    except Exception as e:
        logger.error("rebalance_eval_error", error=str(e))
        return {"status": "error", "message": f"Evaluation failed: {e}"}

    all_decisions = result.get("all_decisions", [])
    for d in all_decisions:
        _state["composite_scores"][d.get("pair", "")] = d.get("composite_score", 0)
        results.append({
            "pair": d.get("pair", ""),
            "action": d.get("action", "HOLD"),
            "composite_score": d.get("composite_score", 0),
            "conviction": d.get("conviction", 0),
            "reasoning": d.get("reasoning", ""),
        })

    # 6) Execute qualifying trades
    trades_executed: list[dict[str, Any]] = []
    decisions = result.get("decisions", [])
    for decision in decisions:
        pair = decision["pair"]
        decision["bot_id"] = "momentum"

        price_data = _state.get("prices", {}).get(pair, {})
        mid_price = price_data.get("mid", 0)
        if mid_price <= 0:
            continue

        decision["entry_price"] = mid_price

        verdict = risk_guard.check("momentum", decision, positions, portfolio)
        if not verdict.approved:
            for r in results:
                if r["pair"] == pair:
                    r["rejected"] = verdict.reason
            continue

        atr = _state.get("indicators_4h", {}).get(pair, {}).get("atr")
        cash = portfolio.get("cash", 0)
        nav = portfolio.get("nav", cash)
        cap = settings.crypto.max_capital
        tradeable = min(nav, cap) if cap > 0 else nav

        notional = 0.0
        if decision["action"] == "BUY":
            sized = compute_position_size(
                pair=pair, bot_id="momentum",
                account_nav=tradeable,
                entry_price=mid_price,
                atr_value=atr or mid_price * 0.02,
            )
            if not sized:
                continue
            notional = sized.notional_usd

            if exit_mgr:
                exit_mgr.register_position(
                    pair, "momentum", mid_price,
                    atr_value=atr or mid_price * 0.02,
                    stop_multiplier=settings.crypto.atr_stop_multiplier,
                    tp_multiplier=settings.crypto.atr_tp_multiplier,
                )

        exec_result = await executor.safe_run(
            decision=decision, price=mid_price, notional=notional,
        )

        trade_record = {
            "pair": pair,
            "action": decision["action"],
            "status": exec_result.get("status", "unknown"),
            "price": exec_result.get("price", mid_price),
            "qty": exec_result.get("qty", 0),
            "notional": notional,
        }

        if exec_result.get("status") == "filled":
            risk_guard.record_trade_time("momentum", pair)
            pnl = exec_result.get("pnl")
            trade_record["pnl"] = pnl
            if pnl is not None:
                if pnl > 0:
                    risk_guard.record_win("momentum")
                    get_loss_tracker().record("momentum", pair, True)
                else:
                    risk_guard.record_loss("momentum")
                    get_loss_tracker().record("momentum", pair, False)
                if decision["action"] == "SELL" and exit_mgr:
                    exit_mgr.remove_position(pair, "momentum")

            _state["recent_trades"].append({
                "pair": pair,
                "side": exec_result.get("side"),
                "qty": exec_result.get("qty", 0),
                "price": exec_result.get("price", mid_price),
                "pnl": pnl or 0,
                "bot_id": "momentum",
                "reasoning": decision.get("reasoning", ""),
                "opened_at": datetime.now(timezone.utc).isoformat(),
            })
            _state["recent_trades"] = _state["recent_trades"][-30:]

        trades_executed.append(trade_record)

    logger.info(
        "rebalance_complete",
        pairs_evaluated=len(all_decisions),
        trades_executed=len(trades_executed),
    )

    return {
        "status": "ok",
        "pairs_evaluated": len(all_decisions),
        "scores": results,
        "trades_executed": trades_executed,
    }


async def tick_1h(telegram: TelegramService, exchange: CoinbaseCryptoService) -> None:
    await asyncio.sleep(60)
    while not _shutdown.is_set():
        try:
            try:
                raw_pos = await asyncio.to_thread(exchange.get_positions)
                positions = await enrich_positions(raw_pos)
            except Exception:
                positions = []

            portfolio = await get_portfolio_state(exchange)
            pos_data = [
                {
                    "pair": p.get("pair", ""),
                    "bot_id": p.get("bot_id", "?"),
                    "qty": p.get("qty", 0),
                    "current_price": p.get("current_price", 0),
                    "unrealized_pnl": p.get("unrealized_pnl", 0),
                }
                for p in positions
            ]
            await telegram.hourly_summary(
                positions=pos_data,
                unrealized_pnl=portfolio.get("unrealized_pnl", 0),
                exposure_pct=portfolio.get("total_exposure_pct", 0),
            )
        except Exception as e:
            logger.error("tick_1h_error", error_msg=str(e))
        await asyncio.sleep(3600)


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
                swing_trades = [t for t in trades if t.bot_id == "swing"]
                day_trades = [t for t in trades if t.bot_id == "day"]
                await telegram.daily_report(
                    total_pnl=total_pnl, win_rate=win_rate, trade_count=len(trades),
                    best_trade=f"Swing: {len(swing_trades)} trades",
                    worst_trade=f"Day: {len(day_trades)} trades",
                )
            else:
                await telegram.send("Daily Report: No closed trades today.")

            if _risk_guard:
                _risk_guard.reset_daily_halt()

        except Exception:
            logger.exception("tick_24h_error")
        await asyncio.sleep(86400)


async def tick_reconcile(
    exchange: CoinbaseCryptoService,
    redis_conn: aioredis.Redis,
) -> None:
    """Reconcile Coinbase fills with local PnL every hour."""
    await asyncio.sleep(300)
    while not _shutdown.is_set():
        try:
            from services.reconciler import reconcile_pnl
            from services.settings_store import load_pnl_summary
            recon = await reconcile_pnl(exchange, redis_conn)
            if "error" not in recon:
                pnl = await load_pnl_summary()
                _state["pnl_summary"] = pnl
                logger.info(
                    "hourly_reconciliation",
                    net_pnl=recon["net_pnl"],
                    fees=recon["total_fees"],
                )
        except Exception as e:
            logger.warning("tick_reconcile_error", error=str(e))
        await asyncio.sleep(3600)


async def tick_daily_backtest(
    exchange: CoinbaseCryptoService,
    redis_conn: aioredis.Redis,
    telegram: TelegramService,
) -> None:
    """Run daily backtest cycle at ~06:00 UTC: replay, score, optimize prompts."""
    await asyncio.sleep(60)
    while not _shutdown.is_set():
        try:
            now = datetime.now(timezone.utc)
            seconds_until_6am = ((6 - now.hour) % 24) * 3600 - now.minute * 60 - now.second
            if seconds_until_6am > 300:
                await asyncio.sleep(min(seconds_until_6am, 3600))
                continue

            logger.info("daily_backtest_starting")
            settings = get_settings()

            from engine.backtester_v2 import replay_day_bot, replay_swing_bot, metrics_to_dict
            from engine.trade_journal import get_recent_entries
            from agents.prompt_optimizer import (
                run_prompt_optimization, store_learnings, get_current_learnings,
            )

            bt_metrics: dict = {}

            for pair in settings.crypto.pair_list[:2]:
                try:
                    bars_5m = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="FIVE_MINUTE",
                        lookback_minutes=24 * 60,
                    )
                    if len(bars_5m) >= 60:
                        async def _day_agent_fn(ind, candles, regime, portfolio):
                            return []
                        day_metrics = await replay_day_bot(bars_5m, _day_agent_fn, sample_every=10)
                        bt_metrics[f"day_{pair}"] = metrics_to_dict(day_metrics)
                except Exception as e:
                    logger.warning("day_backtest_failed", pair=pair, error=str(e))

                try:
                    bars_4h = await asyncio.to_thread(
                        exchange.get_bars, pair, granularity="FOUR_HOUR",
                        lookback_minutes=7 * 24 * 60,
                    )
                    if len(bars_4h) >= 30:
                        async def _swing_agent_fn(ind, candles, regime, portfolio):
                            return []
                        swing_metrics = await replay_swing_bot(bars_4h, _swing_agent_fn)
                        bt_metrics[f"swing_{pair}"] = metrics_to_dict(swing_metrics)
                except Exception as e:
                    logger.warning("swing_backtest_failed", pair=pair, error=str(e))

            journal_entries = await get_recent_entries(days=7, limit=200)

            current_learnings = await get_current_learnings(redis_conn)

            try:
                optimizer_output = await run_prompt_optimization(
                    journal_entries=journal_entries,
                    backtest_metrics=bt_metrics,
                    current_learnings=current_learnings,
                )
                counts = await store_learnings(redis_conn, optimizer_output)
                logger.info("prompt_optimization_complete", learnings=counts)

                report_lines = [
                    "Daily Backtest Report",
                    "",
                ]
                for key, m in bt_metrics.items():
                    report_lines.append(
                        f"{key}: return={m.get('total_return_pct', 0):.1f}%, "
                        f"win_rate={m.get('win_rate', 0):.0%}, "
                        f"trades={m.get('total_trades', 0)}"
                    )
                if optimizer_output.learnings:
                    report_lines.append("")
                    report_lines.append("New Learnings:")
                    for l in optimizer_output.learnings[:5]:
                        report_lines.append(f"  [{l.bot_id}] {l.learning}")

                await telegram.send("\n".join(report_lines))

            except Exception as e:
                logger.error("prompt_optimization_failed", error=str(e))

            _state["backtest_results"] = bt_metrics

        except Exception as e:
            logger.error("daily_backtest_error", error_msg=str(e))

        await asyncio.sleep(86400)


async def display_loop(live: Live, settings) -> None:
    """Update the rich terminal UI every 2 seconds."""
    while not _shutdown.is_set():
        try:
            uptime = int(time.time() - _start_time)
            display = build_full_display(
                prices=_state["prices"],
                portfolio=_state["portfolio"],
                positions=_state["positions"],
                tech_signals=_state.get("indicators_5m", {}),
                fund_signals={},
                news_data=_state["news_data"],
                recent_trades=_state["recent_trades"],
                agent_statuses=_state["agent_statuses"],
                price_history=_state["price_history"],
                healing_events=_state.get("healing_events", []),
                mode="LIVE",
                uptime_sec=uptime,
                regime=_state.get("regime"),
                exchange_status=_state.get("exchange_status", "checking"),
                onchain=_state.get("onchain"),
                microstructure={},
                strategy_signals={},
                backtest={},
                agent_log=_state.get("agent_log"),
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

    from services.settings_store import (
        init_store, load_coinbase_keys, load_trading_settings,
        load_agent_log, load_pnl_summary,
    )
    init_store(redis_conn)

    pnl = await load_pnl_summary()
    _state["pnl_summary"] = pnl
    logger.info("pnl_loaded", total=pnl["total_realized_pnl"], daily=pnl["daily_realized_pnl"])

    redis_keys = await load_coinbase_keys()
    if redis_keys:
        settings.coinbase.api_key = redis_keys["api_key"]
        settings.coinbase.api_secret = redis_keys["api_secret"]

    _state["trading_mode"] = "LIVE"

    redis_trading = await load_trading_settings()
    if redis_trading:
        skip_keys = set()
        if settings.crypto.max_capital == 0 and redis_trading.get("max_capital", 0) > 0:
            skip_keys.add("max_capital")
        for k, v in redis_trading.items():
            if k in skip_keys:
                continue
            if hasattr(settings.crypto, k):
                setattr(settings.crypto, k, type(getattr(settings.crypto, k))(v))

    saved_log = await load_agent_log()
    if saved_log:
        _state["agent_log"] = saved_log

    global _exchange_ref, _risk_guard
    exchange = CoinbaseCryptoService()
    _exchange_ref = exchange

    acct: dict = {}
    if exchange.is_authenticated:
        try:
            acct = await asyncio.to_thread(exchange.get_account)
            _state["exchange_status"] = "connected"
            _state["exchange_error"] = ""
        except Exception as e:
            _state["exchange_status"] = "unauthorized"
            _state["exchange_error"] = str(e).strip()
    else:
        _state["exchange_status"] = "market_only"
        _state["exchange_error"] = exchange.auth_error_message or "CDP PEM keys required"

    telegram = TelegramService()
    price_tracker = PriceTracker(exchange)

    healer = HealerAgent()
    set_healer(healer)
    set_state_ref(_state)

    risk_guard = RiskGuard()
    _risk_guard = risk_guard

    exit_manager = ExitManager()
    global _exit_manager
    _exit_manager = exit_manager

    global _momentum_bot, _news_agent, _executor
    news_agent = NewsScoutAgent()
    swing_bot = SwingSniperAgent()
    momentum_bot = MomentumTraderAgent()
    executor = OrderExecutorAgent(exchange, telegram)
    _momentum_bot = momentum_bot
    _news_agent = news_agent
    _executor = executor

    # Reconcile PnL from Coinbase fills on startup
    if exchange.is_authenticated:
        try:
            from services.reconciler import reconcile_pnl
            recon = await reconcile_pnl(exchange, redis_conn)
            if "error" not in recon:
                pnl = await load_pnl_summary()
                _state["pnl_summary"] = pnl
                logger.info(
                    "startup_reconciliation_done",
                    net_pnl=recon["net_pnl"],
                    fees=recon["total_fees"],
                    fills=recon.get("total_trades", 0),
                )
        except Exception as e:
            logger.warning("startup_reconciliation_failed", error=str(e))

    _state["equity_curve"] = []

    init_nav = float(acct.get("portfolio_value", acct.get("equity", 0))) if acct else 0
    init_cash = float(acct.get("cash", 0)) if acct else 0
    cap = settings.crypto.max_capital
    cap_label = f"${init_nav:,.2f} (whole account)" if cap <= 0 else f"${cap:,.0f}"

    _state["portfolio"] = {
        "nav": init_nav, "cash": init_cash,
        "total_exposure_pct": 0, "unrealized_pnl": 0, "drawdown_pct": 0,
    }

    await telegram.send(
        "Alpha-Paca Crypto v3 Started (Adaptive Momentum + SwingSniper)\n"
        f"Pairs: {', '.join(settings.crypto.pair_list)}\n"
        f"Capital: {cap_label}\n"
        f"Buy Threshold: {settings.crypto.composite_buy_threshold} | "
        f"Exit: {settings.crypto.composite_exit_threshold}\n"
        f"Risk/Trade: {settings.crypto.max_risk_per_trade_pct}% | "
        f"Daily Halt: {settings.crypto.daily_loss_halt_pct}%\n"
        f"Mode: LIVE"
    )

    init_web(_state, _start_time, settings)

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
        task_factories = {
            "heartbeat": lambda: heartbeat_loop(redis_conn),
            "tick_15s": lambda: tick_15s(price_tracker, exchange),
            "momentum_trader": lambda: tick_momentum_trader(momentum_bot, risk_guard, executor, exit_manager, exchange),
            "exit_manager": lambda: tick_exit_manager(exit_manager, executor, exchange),
            "swing_sniper": lambda: tick_swing_sniper(swing_bot, risk_guard, executor, price_tracker, exchange),
            "swing_exit": lambda: tick_swing_exit_check(executor, exchange),
            "news_fast": lambda: tick_news_fast(news_agent),
            "onchain": lambda: tick_onchain(),
            "tick_1h": lambda: tick_1h(telegram, exchange),
            "tick_24h": lambda: tick_24h(telegram),
            "daily_backtest": lambda: tick_daily_backtest(exchange, redis_conn, telegram),
            "reconcile": lambda: tick_reconcile(exchange, redis_conn),
            "web": lambda: _safe_web_serve(),
        }
        if is_tty and live_ctx:
            task_factories["display"] = lambda: display_loop(live_ctx, settings)

        core_tasks: dict[str, asyncio.Task] = {}
        for name, factory in task_factories.items():
            core_tasks[name] = asyncio.create_task(factory(), name=name)

        while not _shutdown.is_set():
            for name, task in list(core_tasks.items()):
                if task.done() and not _shutdown.is_set():
                    exc = task.exception() if not task.cancelled() else None
                    logger.error("task_died_restarting", task=name,
                                 error=str(exc) if exc else "cancelled")
                    core_tasks[name] = asyncio.create_task(
                        task_factories[name](), name=name
                    )
            await asyncio.sleep(5)

        web_server.should_exit = True
        for t in core_tasks.values():
            t.cancel()
        await asyncio.gather(*core_tasks.values(), return_exceptions=True)

    if live_ctx:
        with live_ctx:
            await _run_tasks()
    else:
        logger.info("web_dashboard_available", url=f"http://0.0.0.0:{web_port}")
        await _run_tasks()

    await price_tracker.close()
    await redis_conn.aclose()
    await engine.dispose()
    await telegram.send("Alpha-Paca Crypto v3 Stopped")


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(run())


if __name__ == "__main__":
    main()

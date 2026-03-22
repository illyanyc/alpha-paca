"""Alpha-Paca Crypto — main async event loop, scheduler, and rich terminal UI."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog
import uvicorn
from rich.console import Console
from rich.live import Live
import sqlalchemy
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
from engine.backtester import run_backtest_cycle
from engine.learner import AdaptiveLearner
from engine.microstructure import MicrostructureEngine
from engine.position_sizer import compute_position_size
from engine.regime import detect_regime
from engine.strategies import run_all_strategies
from services.coinbase_crypto import CoinbaseCryptoService
from services.onchain_client import fetch_all_onchain
from services.price_tracker import PriceTracker
from services.telegram import TelegramService
from web import app as web_app, init_web

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
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
TICK_5M = 120
TICK_1H = 3600
TICK_24H = 86400

_shutdown = asyncio.Event()
_start_time = time.time()

# ── Shared mutable state for display ─────────────────────────────────
_state: dict[str, Any] = {
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
    "agent_log": [],
    "strategy_signals": {},
    "backtest_results": {},
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
}


_exchange_ref: CoinbaseCryptoService | None = None
_learner: AdaptiveLearner | None = None


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
        logger.warning("coinbase_keys_reload_failed", error=err)
        return {"status": "unauthorized", "error": err}


async def update_trading_settings(new_settings: dict) -> dict[str, str]:
    """Update trading parameters in-memory and persist to Redis."""
    from services.settings_store import save_trading_settings

    settings = get_settings()
    updated = []
    if "max_capital" in new_settings:
        settings.crypto.max_capital = float(new_settings["max_capital"])
        updated.append("max_capital")
    if "risk_per_trade_pct" in new_settings:
        settings.crypto.risk_per_trade_pct = float(new_settings["risk_per_trade_pct"])
        updated.append("risk_per_trade_pct")
    if "max_position_pct" in new_settings:
        settings.crypto.max_position_pct = float(new_settings["max_position_pct"])
        updated.append("max_position_pct")
    if "max_drawdown_pct" in new_settings:
        settings.crypto.max_drawdown_pct = float(new_settings["max_drawdown_pct"])
        updated.append("max_drawdown_pct")
    if "max_total_exposure_pct" in new_settings:
        settings.crypto.max_total_exposure_pct = float(new_settings["max_total_exposure_pct"])
        updated.append("max_total_exposure_pct")
    if "confidence_threshold" in new_settings:
        settings.crypto.confidence_threshold = float(new_settings["confidence_threshold"])
        updated.append("confidence_threshold")
    if "pairs" in new_settings:
        settings.crypto.pairs = str(new_settings["pairs"])
        updated.append("pairs")

    persisted = {
        "max_capital": settings.crypto.max_capital,
        "risk_per_trade_pct": settings.crypto.risk_per_trade_pct,
        "max_position_pct": settings.crypto.max_position_pct,
        "max_drawdown_pct": settings.crypto.max_drawdown_pct,
        "max_total_exposure_pct": settings.crypto.max_total_exposure_pct,
        "confidence_threshold": settings.crypto.confidence_threshold,
        "pairs": settings.crypto.pairs,
    }
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
        await conn.execute(
            sqlalchemy.text(
                "ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS side VARCHAR(10) NOT NULL DEFAULT 'long'"
            )
        )
        await conn.execute(
            sqlalchemy.text(
                "ALTER TABLE crypto_trades ADD COLUMN IF NOT EXISTS exchange_order_id VARCHAR(64)"
            )
        )


async def enrich_positions(exchange_positions: list[dict]) -> list[dict]:
    """Cross-reference exchange holdings with DB entry prices for accurate PnL."""
    async with async_session_factory() as session:
        stmt = select(CryptoPosition)
        result = await session.execute(stmt)
        db_positions = {p.pair: p for p in result.scalars().all()}

    enriched = []

    for ep in exchange_positions:
        pair = ep.get("symbol", ep.get("pair", ""))
        current_price = float(ep.get("current_price", 0))
        qty = float(ep.get("qty", 0))

        db_pos = db_positions.get(pair)
        if db_pos and db_pos.avg_entry_price > 0:
            entry_price = float(db_pos.avg_entry_price)
            side = db_pos.side or "long"
        else:
            entry_price = current_price
            side = "long"

        if side == "short":
            unrealized_pnl = (entry_price - current_price) * qty
            unrealized_pnl_pct = ((entry_price - current_price) / entry_price * 100) if entry_price > 0 else 0
        else:
            unrealized_pnl = (current_price - entry_price) * qty
            unrealized_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        market_value = qty * current_price

        enriched.append({
            **ep,
            "pair": pair,
            "side": side,
            "avg_entry_price": entry_price,
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "market_value": market_value,
            "market_value_usd": market_value,
        })

    for pair, db_pos in db_positions.items():
        if db_pos.side == "short" and db_pos.qty > 0:
            already = any(e["pair"] == pair for e in enriched)
            if not already:
                entry_price = float(db_pos.avg_entry_price)
                current_price = float(db_pos.current_price)
                qty = float(db_pos.qty)
                unrealized_pnl = (entry_price - current_price) * qty
                unrealized_pnl_pct = ((entry_price - current_price) / entry_price * 100) if entry_price > 0 else 0
                enriched.append({
                    "pair": pair, "symbol": pair, "side": "short",
                    "qty": qty, "avg_entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "market_value": qty * current_price,
                    "market_value_usd": qty * current_price,
                })

    return enriched


async def sync_positions_to_db(enriched_positions: list[dict]) -> None:
    """Keep DB CryptoPosition rows in sync with actual exchange state."""
    from decimal import Decimal as D

    async with async_session_factory() as session:
        stmt = select(CryptoPosition)
        result = await session.execute(stmt)
        db_map = {p.pair: p for p in result.scalars().all()}

        exchange_pairs = set()
        for ep in enriched_positions:
            pair = ep.get("pair", "")
            if not pair:
                continue
            exchange_pairs.add(pair)
            qty = D(str(ep.get("qty", 0)))
            current_price = D(str(ep.get("current_price", 0)))
            entry_price = D(str(ep.get("avg_entry_price", 0)))
            mv = D(str(ep.get("market_value_usd", 0)))
            pnl = D(str(ep.get("unrealized_pnl", 0)))

            side = ep.get("side", "long")

            if pair in db_map:
                pos = db_map[pair]
                pos.current_price = current_price
                pos.market_value_usd = mv
                pos.unrealized_pnl = pnl
                if pos.qty == 0 and qty > 0:
                    pos.qty = qty
                    pos.avg_entry_price = entry_price
                    pos.side = side
            else:
                session.add(CryptoPosition(
                    pair=pair,
                    side=side,
                    qty=qty,
                    avg_entry_price=entry_price,
                    current_price=current_price,
                    market_value_usd=mv,
                    unrealized_pnl=pnl,
                ))

        for db_pair, db_pos in db_map.items():
            if db_pair not in exchange_pairs and db_pos.side != "short":
                db_pos.qty = D(0)
                db_pos.current_price = D(0)
                db_pos.market_value_usd = D(0)
                db_pos.unrealized_pnl = D(0)

        await session.commit()


_high_water_mark: float = 0.0


async def get_portfolio_state(exchange: CoinbaseCryptoService) -> dict:
    """Fetch real Coinbase account equity and positions.

    NAV always reflects the true Coinbase account value.
    max_capital is only used to cap *tradeable* capital in position sizing,
    never to distort NAV reporting.
    """
    global _high_water_mark
    settings = get_settings()
    try:
        acct = await asyncio.to_thread(exchange.get_account)
        raw_positions = await asyncio.to_thread(exchange.get_positions)
        positions = await enrich_positions(raw_positions)
    except Exception as e:
        logger.error("portfolio_state_error", error=str(e), exc_info=True)
        prev = _state.get("portfolio", {})
        return {
            "nav": prev.get("nav", 0),
            "cash": prev.get("cash", 0),
            "total_exposure_pct": prev.get("total_exposure_pct", 0),
            "unrealized_pnl": prev.get("unrealized_pnl", 0),
            "drawdown_pct": prev.get("drawdown_pct", 0),
            "positions_count": prev.get("positions_count", 0),
        }

    total_mv = sum(float(p.get("market_value_usd", p.get("market_value", 0))) for p in positions)
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


async def heartbeat_loop(redis_conn: aioredis.Redis) -> None:
    while not _shutdown.is_set():
        await redis_conn.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=120)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def tick_30s(
    tech_agent: TechnicalAnalystAgent,
    price_tracker: PriceTracker,
    exchange: CoinbaseCryptoService,
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

            settings = get_settings()
            r = await tech_agent._get_redis()

            regime_str = None
            try:
                first_pair = settings.crypto.pair_list[0]
                hourly_bars = await asyncio.to_thread(exchange.get_bars, first_pair, granularity="ONE_HOUR", lookback_minutes=168 * 60)
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
                    regime_str = regime_state.regime.value
                    await r.set("crypto:regime", json.dumps(regime_dict), ex=600)
            except Exception:
                logger.debug("regime_detection_skipped")

            micro_engine: MicrostructureEngine = _state.get("micro_engine")
            if micro_engine:
                micro_states = micro_engine.get_all_states()
                micro_dict = {}
                for pair, ms in micro_states.items():
                    micro_dict[pair] = {
                        "signal": ms.signal, "score": ms.score,
                        "imbalance": ms.bid_ask_imbalance,
                        "flow": ms.trade_flow_imbalance,
                        "vpin": ms.vpin, "spread_bps": ms.spread_bps,
                    }
                _state["microstructure"] = micro_dict
                await r.set("crypto:microstructure", json.dumps(micro_dict), ex=120)

            strat_signals: dict[str, list] = {}
            for pair in settings.crypto.pair_list:
                try:
                    bars = await asyncio.to_thread(exchange.get_bars, pair, lookback_minutes=120)
                    if len(bars) >= 30:
                        from engine.indicators import compute_all
                        ind = compute_all(bars)
                        micro_data = _state.get("microstructure", {}).get(pair, {})
                        onchain_data = _state.get("onchain", {})
                        if hasattr(onchain_data, "__dict__"):
                            onchain_data = {"btc_funding": onchain_data.btc_funding_rate}
                        sigs = run_all_strategies(
                            bars, ind, regime=regime_str,
                            microstructure=micro_data,
                            onchain=onchain_data if isinstance(onchain_data, dict) else {},
                        )
                        strat_signals[pair] = sigs
                except Exception:
                    pass
            if strat_signals:
                _state["strategy_signals"] = strat_signals
                await r.set("crypto:signals:strategies", json.dumps(strat_signals), ex=120)

            portfolio = await get_portfolio_state(exchange)
            _state["portfolio"] = portfolio

            nav = portfolio.get("nav", 0)
            if nav > 0:
                curve = _state.get("equity_curve", [])
                curve.append({"ts": datetime.now(timezone.utc).isoformat(), "nav": nav})
                if len(curve) > 2880:
                    _state["equity_curve"] = curve[-2880:]

            try:
                raw_pos = await asyncio.to_thread(exchange.get_positions)
                enriched = await enrich_positions(raw_pos)
                _state["positions"] = enriched
                await sync_positions_to_db(enriched)
            except Exception as pos_err:
                logger.warning("positions_fetch_failed", error=str(pos_err))
        except Exception as e:
            logger.exception("tick_30s_error", error_msg=str(e))
        await asyncio.sleep(TICK_30S)


async def check_protective_exits(
    exchange: CoinbaseCryptoService,
    executor: OrderExecutorAgent,
) -> None:
    """Run stop-loss and take-profit checks every 30s against held positions."""
    await asyncio.sleep(20)
    while not _shutdown.is_set():
        try:
            settings = get_settings()
            positions = _state.get("positions", [])

            for pos in positions:
                pair = pos.get("pair", pos.get("symbol", ""))
                pnl_pct = float(pos.get("unrealized_pnl_pct", 0))
                qty = float(pos.get("qty", 0))
                side = pos.get("side", "long")
                if qty <= 0 or not pair:
                    continue

                exit_action = "COVER" if side == "short" else "SELL"

                if pnl_pct <= -settings.crypto.stop_loss_pct:
                    logger.warning(
                        "stop_loss_triggered", pair=pair, side=side,
                        pnl_pct=pnl_pct, threshold=-settings.crypto.stop_loss_pct,
                    )
                    await executor.safe_run(
                        decision={"action": exit_action, "pair": pair, "size_pct": 100,
                                  "confidence": 0.99,
                                  "reasoning": f"STOP-LOSS ({side}): {pnl_pct:.1f}% loss exceeds -{settings.crypto.stop_loss_pct}% limit"},
                        price=float(pos.get("current_price", 0)),
                        available_capital=0,
                    )

                elif pnl_pct >= settings.crypto.take_profit_pct:
                    logger.info(
                        "take_profit_triggered", pair=pair, side=side,
                        pnl_pct=pnl_pct, threshold=settings.crypto.take_profit_pct,
                    )
                    await executor.safe_run(
                        decision={"action": exit_action, "pair": pair, "size_pct": 100,
                                  "confidence": 0.95,
                                  "reasoning": f"TAKE-PROFIT ({side}): {pnl_pct:.1f}% gain exceeds +{settings.crypto.take_profit_pct}% target"},
                        price=float(pos.get("current_price", 0)),
                        available_capital=0,
                    )

        except Exception as e:
            logger.exception("protective_exit_error", error_msg=str(e))
        await asyncio.sleep(TICK_30S)


async def tick_5m(
    news_agent: NewsScoutAgent,
    fund_agent: FundamentalAnalystAgent,
    orchestrator: OrchestratorAgent,
    risk_agent: RiskValidatorAgent,
    executor: OrderExecutorAgent,
    price_tracker: PriceTracker,
    exchange: CoinbaseCryptoService,
) -> None:
    await asyncio.sleep(10)
    while not _shutdown.is_set():
        try:
            news_result, fund_result, onchain = await asyncio.gather(
                news_agent.safe_run(),
                fund_agent.safe_run(),
                fetch_all_onchain(),
            )

            if isinstance(news_result, dict) and "error" not in news_result:
                _state["news_data"] = news_result

            if isinstance(fund_result, dict) and "error" not in fund_result:
                _state["fund_signals"] = fund_result

            if onchain:
                oc_dict = {
                    "fear_greed_index": onchain.fear_greed_index,
                    "fear_greed_label": onchain.fear_greed_label,
                    "btc_funding_rate": onchain.btc_funding_rate,
                    "eth_funding_rate": onchain.eth_funding_rate,
                    "signal": onchain.signal,
                    "score": onchain.score,
                }
                _state["onchain"] = oc_dict
                try:
                    r = await news_agent._get_redis()
                    await r.set("crypto:onchain", json.dumps(oc_dict), ex=600)
                except Exception:
                    pass

            try:
                raw_pos = await asyncio.to_thread(exchange.get_positions)
                positions = await enrich_positions(raw_pos)
                _state["positions"] = positions
            except Exception as pos_err:
                logger.warning("positions_fetch_failed", error=str(pos_err), tick="5m")
                positions = _state.get("positions", [])

            portfolio = await get_portfolio_state(exchange)
            _state["portfolio"] = portfolio

            learning_summary = _learner.get_learning_summary() if _learner else {}

            orch_result = await orchestrator.safe_run(
                positions=positions,
                portfolio_state=portfolio,
                learning_summary=learning_summary,
            )

            all_decisions = orch_result.get("all_decisions", [])
            decisions = orch_result.get("decisions", [])
            outlook = orch_result.get("market_outlook", "unknown")
            summary = orch_result.get("summary", "")

            logger.info(
                "orchestrator_cycle",
                outlook=outlook,
                total=len(all_decisions),
                actionable=len(decisions),
                summary=summary[:120],
            )

            if not decisions:
                _state["agent_statuses"]["risk_validator"] = "standby"
                _state["agent_statuses"]["order_executor"] = "standby"

            for decision in decisions:
                logger.info(
                    "executing_decision",
                    pair=decision.get("pair"),
                    action=decision.get("action"),
                    confidence=decision.get("confidence"),
                )

                risk_result = await risk_agent.safe_run(
                    decision=decision,
                    positions=positions,
                    portfolio_state=portfolio,
                )

                if not risk_result.get("approved", False):
                    logger.info("trade_rejected_by_risk", pair=decision.get("pair"), reasons=risk_result.get("reasons"))
                    continue

                prices = await price_tracker.get_all_cached_prices()
                pair = decision.get("pair", "")
                mid_price = prices.get(pair, {}).get("mid", 0)
                if mid_price <= 0:
                    continue

                cash = portfolio.get("cash", 0)
                cap = settings.crypto.max_capital
                tradeable = min(cash, cap) if cap > 0 else cash

                exec_result = await executor.safe_run(
                    decision=decision,
                    price=mid_price,
                    available_capital=tradeable,
                )

                logger.info(
                    "executor_result",
                    pair=pair,
                    status=exec_result.get("status"),
                    error=exec_result.get("error"),
                    qty=exec_result.get("qty"),
                    price=exec_result.get("price"),
                )

                if exec_result.get("status") == "filled":
                    trade_entry = {
                        "pair": pair,
                        "side": exec_result.get("side", decision.get("action")),
                        "qty": exec_result.get("qty", 0),
                        "price": exec_result.get("price", mid_price),
                        "pnl": exec_result.get("pnl", 0),
                        "reasoning": decision.get("reasoning", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _state["recent_trades"].append(trade_entry)
                    _state["recent_trades"] = _state["recent_trades"][-20:]

                    if _learner and exec_result.get("pnl") is not None:
                        pnl_pct = exec_result.get("pnl_pct", 0)
                        strat_sigs = _state.get("strategy_signals", {}).get(pair, [])
                        strat_dict = {s["name"]: s for s in strat_sigs if isinstance(s, dict)}
                        _learner.record_trade(
                            pair=pair,
                            side=trade_entry["side"],
                            pnl_pct=pnl_pct,
                            strategy_signals=strat_dict,
                            confidence=decision.get("confidence", 0),
                        )

            from services.settings_store import save_agent_log as _sal, load_pnl_summary
            await _sal(_state.get("agent_log", []))
            _state["pnl_summary"] = await load_pnl_summary()

            if _learner:
                r = await news_agent._get_redis()
                await _learner.save(r)

        except Exception as e:
            logger.exception("tick_5m_error", error_msg=str(e))
        await asyncio.sleep(TICK_5M)


async def tick_backtest(exchange: CoinbaseCryptoService, redis_conn: aioredis.Redis) -> None:
    """Run backtests every hour to update strategy weights."""
    await asyncio.sleep(30)
    while not _shutdown.is_set():
        try:
            settings = get_settings()
            bt_result = await run_backtest_cycle(
                pairs=settings.crypto.pair_list,
                get_bars_fn=exchange.get_bars,
                redis_conn=redis_conn,
            )
            _state["backtest_results"] = bt_result

            if _learner:
                bt_weights = bt_result.get("strategy_weights", {})
                adaptive = _learner.get_adaptive_weights(bt_weights)
                logger.info(
                    "adaptive_weights_updated",
                    weights={k: round(v, 2) for k, v in adaptive.items()},
                )
        except Exception as e:
            logger.exception("backtest_error", error_msg=str(e))
        await asyncio.sleep(3600)


async def tick_1h(telegram: TelegramService, exchange: CoinbaseCryptoService) -> None:
    await asyncio.sleep(60)
    while not _shutdown.is_set():
        try:
            try:
                raw_pos = await asyncio.to_thread(exchange.get_positions)
                positions = await enrich_positions(raw_pos)
            except Exception as pos_err:
                logger.warning("positions_fetch_failed", error=str(pos_err), tick="1h")
                positions = []

            portfolio = await get_portfolio_state(exchange)
            pos_data = [
                {
                    "pair": p.get("pair", p.get("symbol", "")),
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
            logger.exception("tick_1h_error", error_msg=str(e))
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
    mode = "LIVE"
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
                regime=_state.get("regime"),
                exchange_status=_state.get("exchange_status", "checking"),
                onchain=_state.get("onchain"),
                microstructure=_state.get("microstructure"),
                strategy_signals=_state.get("strategy_signals"),
                backtest=_state.get("backtest_results"),
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
        load_agent_log, save_agent_log, load_pnl_summary,
    )
    init_store(redis_conn)

    pnl = await load_pnl_summary()
    _state["pnl_summary"] = pnl
    logger.info("pnl_loaded_from_redis", total=pnl["total_realized_pnl"], daily=pnl["daily_realized_pnl"])

    redis_keys = await load_coinbase_keys()
    if redis_keys:
        logger.info("using_redis_coinbase_keys")
        settings.coinbase.api_key = redis_keys["api_key"]
        settings.coinbase.api_secret = redis_keys["api_secret"]

    _state["trading_mode"] = "LIVE"

    redis_trading = await load_trading_settings()
    if redis_trading:
        skip_keys = set()
        if settings.crypto.max_capital == 0 and redis_trading.get("max_capital", 0) > 0:
            skip_keys.add("max_capital")
            logger.info("redis_max_capital_skipped",
                        hint="env CRYPTO_MAX_CAPITAL=0 (whole account) takes precedence over Redis value")
        logger.info("using_redis_trading_settings", keys=list(redis_trading.keys()), skipped=list(skip_keys))
        for k, v in redis_trading.items():
            if k in skip_keys:
                continue
            if hasattr(settings.crypto, k):
                setattr(settings.crypto, k, type(getattr(settings.crypto, k))(v))

    saved_log = await load_agent_log()
    if saved_log:
        _state["agent_log"] = saved_log
        logger.info("agent_log_loaded_from_redis", entries=len(saved_log))

    global _exchange_ref
    exchange = CoinbaseCryptoService()
    _exchange_ref = exchange

    acct: dict = {}
    if exchange.is_authenticated:
        try:
            acct = await asyncio.to_thread(exchange.get_account)
            logger.info(
                "coinbase_connected",
                equity=acct.get("equity"),
                cash=acct.get("cash"),
            )
            _state["exchange_status"] = "connected"
            _state["exchange_error"] = ""
        except Exception as e:
            err_msg = str(e).strip()
            logger.error("coinbase_auth_failed", error=err_msg)
            _state["exchange_status"] = "unauthorized"
            _state["exchange_error"] = err_msg
    else:
        err_msg = exchange.auth_error_message or "CDP PEM keys required for trading"
        logger.warning(
            "coinbase_no_trading_auth",
            hint="Market data available. Trading disabled.",
            error=err_msg,
        )
        _state["exchange_status"] = "market_only"
        _state["exchange_error"] = err_msg

    telegram = TelegramService()
    price_tracker = PriceTracker(exchange)

    healer = HealerAgent()
    set_healer(healer)
    set_state_ref(_state)

    global _learner
    _learner = AdaptiveLearner()
    await _learner.load(redis_conn)

    news_agent = NewsScoutAgent()
    tech_agent = TechnicalAnalystAgent(exchange)
    fund_agent = FundamentalAnalystAgent(exchange)
    orchestrator = OrchestratorAgent()
    risk_agent = RiskValidatorAgent()
    executor = OrderExecutorAgent(exchange, telegram)

    micro_engine = MicrostructureEngine()
    _state["micro_engine"] = micro_engine
    _state["equity_curve"] = []

    init_nav = float(acct.get("portfolio_value", acct.get("equity", 0))) if acct else 0
    init_cash = float(acct.get("cash", 0)) if acct else 0
    cap = settings.crypto.max_capital
    cap_label = f"${init_nav:,.2f} (whole account)" if cap <= 0 else f"${cap:,.0f} cap / ${init_nav:,.2f} acct"

    _state["portfolio"] = {
        "nav": init_nav,
        "cash": init_cash,
        "total_exposure_pct": 0,
        "unrealized_pnl": 0,
        "drawdown_pct": 0,
    }

    await telegram.send(
        "🚀 *Alpha-Paca Crypto Started (Coinbase)*\n"
        f"Pairs: {', '.join(settings.crypto.pair_list)}\n"
        f"Capital: {cap_label}\n"
        f"Mode: LIVE"
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
            asyncio.create_task(tick_30s(tech_agent, price_tracker, exchange), name="tick_30s"),
            asyncio.create_task(tick_5m(
                news_agent, fund_agent, orchestrator, risk_agent, executor, price_tracker, exchange
            ), name="tick_5m"),
            asyncio.create_task(check_protective_exits(exchange, executor), name="protective_exits"),
            asyncio.create_task(tick_1h(telegram, exchange), name="tick_1h"),
            asyncio.create_task(tick_backtest(exchange, redis_conn), name="backtest"),
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

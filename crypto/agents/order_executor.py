"""OrderExecutorAgent — submits orders to Coinbase, monitors fills, records to DB.

Bot_id-aware: each trade and position is tagged with the originating bot.
For the momentum trader, entries use limit orders (post_only=true for maker fees)
with bracket TP/SL.  Falls back to market orders if limit doesn't fill within 60s.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import and_, select

from agents.base import BaseAgent
from db.engine import async_session_factory
from db.models import CryptoPosition, CryptoTrade
from services.coinbase_crypto import CoinbaseCryptoService
from services.settings_store import record_realized_pnl
from services.telegram import TelegramService

logger = structlog.get_logger(__name__)


class OrderExecutorAgent(BaseAgent):
    name = "order_executor"

    def __init__(self, exchange: CoinbaseCryptoService, telegram: TelegramService) -> None:
        super().__init__()
        self._exchange = exchange
        self._telegram = telegram

    async def run(self, **kwargs) -> dict:
        """Execute a trade decision.

        kwargs expected:
            decision: dict with action, pair, confidence, reasoning, bot_id,
                      target_price, stop_price
            notional: float — dollar amount to buy (from LeverageSizer)
            price: current mid price for the pair
        """
        decision = kwargs.get("decision", {})
        price = kwargs.get("price", 0)
        notional = kwargs.get("notional", 0)

        action = decision.get("action", "HOLD")
        pair = decision.get("pair", "")
        confidence = decision.get("confidence", 0)
        reasoning = decision.get("reasoning", "")
        bot_id = decision.get("bot_id", "swing")
        target_price = decision.get("target_price")
        stop_price = decision.get("stop_price")

        if action == "HOLD":
            return {"status": "no_action", "pair": pair}

        if action == "BUY":
            self.think(f"[{bot_id}] BUY {pair}: ${notional:,.0f} @ ${price:,.2f} (conv={confidence:.2f})")
            return await self._execute_buy(
                pair, notional, price, confidence, reasoning, bot_id,
                target_price, stop_price,
            )
        elif action == "SELL":
            self.think(f"[{bot_id}] SELL {pair} (conv={confidence:.2f})")
            return await self._execute_sell(pair, confidence, reasoning, bot_id)

        return {"status": "unknown_action", "action": action}

    async def _execute_buy(
        self,
        pair: str,
        notional: float,
        price: float,
        confidence: float,
        reasoning: str,
        bot_id: str,
        target_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict:
        qty = Decimal(str(notional / price)) if price > 0 else Decimal(0)
        if qty <= 0:
            return {"status": "skip", "reason": "zero qty"}

        use_limit = bot_id == "momentum" and target_price and stop_price

        try:
            logger.info(
                "submitting_buy", pair=pair, qty=str(qty),
                notional=notional, bot=bot_id,
                order_type="limit+bracket" if use_limit else "market",
            )

            if use_limit:
                order_result = await self._try_limit_buy(
                    pair, qty, price, target_price, stop_price, bot_id,
                )
            else:
                order_result = await asyncio.to_thread(
                    self._exchange.submit_market_order, pair, qty, "BUY",
                )

            self.think(f"[{bot_id}] Order submitted {pair} BUY — waiting for fill")
            fill = await self._exchange.wait_for_fill(order_result["order_id"])

            if fill.get("status") in ("cancelled", "expired"):
                if use_limit:
                    self.think(f"[{bot_id}] {pair} limit order {fill.get('status')} — fallback to market")
                    order_result = await asyncio.to_thread(
                        self._exchange.submit_market_order, pair, qty, "BUY",
                    )
                    fill = await self._exchange.wait_for_fill(order_result["order_id"])
                    if fill.get("status") in ("cancelled", "expired"):
                        self.think(f"[{bot_id}] {pair} BUY market fallback also {fill.get('status')}")
                        return {"status": fill["status"], "pair": pair, "reason": f"order {fill['status']}"}
                else:
                    self.think(f"[{bot_id}] {pair} BUY order {fill.get('status')} — skipping")
                    return {"status": fill["status"], "pair": pair, "reason": f"order {fill['status']}"}

            filled_price = fill.get("filled_avg_price", 0)
            filled_qty = fill.get("filled_qty", 0)

            if filled_qty <= 0 or filled_price <= 0:
                self.think(f"[{bot_id}] {pair} BUY: zero fill — skipping")
                return {"status": "skip", "pair": pair, "reason": "zero fill"}
            slippage_bps = ((filled_price - price) / price * 10000) if price > 0 else 0

            self.think(f"[{bot_id}] FILLED {pair} BUY: {filled_qty} @ ${filled_price:,.2f} (slip={slippage_bps:.1f}bps)")

            await self._record_trade(
                pair=pair, side="BUY", qty=Decimal(str(filled_qty)),
                price=Decimal(str(filled_price)), confidence=confidence,
                reasoning=reasoning, slippage_bps=slippage_bps,
                order_id=order_result["order_id"], bot_id=bot_id,
                target_price=target_price, stop_price=stop_price,
            )

            await self._update_position(
                pair, Decimal(str(filled_qty)), Decimal(str(filled_price)),
                is_buy=True, bot_id=bot_id,
            )

            await self._telegram.trade_alert(
                pair=pair, side=f"BUY [{bot_id}]", qty=filled_qty,
                price=filled_price, confidence=confidence, reasoning=reasoning,
            )

            return {
                "status": "filled", "pair": pair, "side": "BUY",
                "qty": filled_qty, "price": filled_price,
                "slippage_bps": slippage_bps, "order_id": order_result["order_id"],
                "bot_id": bot_id,
            }

        except Exception as e:
            logger.exception("buy_failed", pair=pair, bot=bot_id, error=str(e))
            self.think(f"[{bot_id}] BUY FAILED {pair}: {e}")
            await self._telegram.error_alert("Buy Failed", f"[{bot_id}] {pair}: {e}")
            return {"status": "error", "pair": pair, "error": str(e)}

    async def _try_limit_buy(
        self,
        pair: str,
        qty: Decimal,
        current_price: float,
        target_price: float,
        stop_price: float,
        bot_id: str,
    ) -> dict:
        """Attempt a limit+bracket order, fall back to plain limit if bracket fails."""
        try:
            return await asyncio.to_thread(
                self._exchange.submit_bracket_order,
                pair, qty, current_price, target_price, stop_price, True,
            )
        except Exception as e:
            self.think(f"[{bot_id}] Bracket order unavailable ({e}), using limit only")
            return await asyncio.to_thread(
                self._exchange.submit_limit_order,
                pair, qty, "BUY", current_price, True,
            )

    async def _execute_sell(self, pair: str, confidence: float, reasoning: str, bot_id: str) -> dict:
        """Sell the bot's position for a pair."""
        try:
            qty: Decimal = Decimal(0)
            entry_price: Decimal = Decimal(0)
            current_price_ref: float = 0.0

            async with async_session_factory() as session:
                stmt = select(CryptoPosition).where(
                    and_(CryptoPosition.pair == pair, CryptoPosition.bot_id == bot_id)
                )
                result = await session.execute(stmt)
                position = result.scalar_one_or_none()

            if position and position.qty > 0:
                qty = position.qty
                entry_price = position.avg_entry_price
                current_price_ref = float(position.current_price)
            else:
                exchange_positions = await asyncio.to_thread(self._exchange.get_positions)
                for ep in exchange_positions:
                    ep_pair = ep.get("pair", ep.get("symbol", ""))
                    if ep_pair == pair and float(ep.get("qty", 0)) > 0:
                        qty = Decimal(str(ep["qty"]))
                        entry_price = Decimal(str(ep.get("avg_entry_price", ep.get("current_price", 0))))
                        current_price_ref = float(ep.get("current_price", 0))
                        break

            if qty <= 0:
                self.think(f"[{bot_id}] {pair} SELL skipped — no position")
                return {"status": "skip", "reason": "no position to sell"}

            actual_balance = await asyncio.to_thread(self._exchange.get_available_balance, pair)
            if actual_balance <= 0:
                self.think(f"[{bot_id}] {pair} SELL skipped — zero balance on exchange")
                return {"status": "skip", "reason": "zero exchange balance"}
            if actual_balance < qty:
                qty = actual_balance

            qty = self._exchange._quantize_qty(pair, qty)
            if qty <= 0:
                return {"status": "skip", "reason": "qty too small"}

            order_result = await asyncio.to_thread(self._exchange.submit_market_order, pair, qty, "SELL")
            self.think(f"[{bot_id}] Order submitted {pair} SELL — waiting for fill")
            fill = await self._exchange.wait_for_fill(order_result["order_id"])

            if fill.get("status") in ("cancelled", "expired"):
                self.think(f"[{bot_id}] {pair} SELL order {fill.get('status')} — not recording PnL")
                return {"status": fill["status"], "pair": pair, "reason": f"order {fill['status']}"}

            filled_price = fill.get("filled_avg_price", 0)
            filled_qty = fill.get("filled_qty", 0)

            if filled_qty <= 0 or filled_price <= 0:
                self.think(f"[{bot_id}] {pair} SELL: zero fill (qty={filled_qty}, price={filled_price}) — skipping PnL")
                return {"status": "skip", "pair": pair, "reason": "zero fill qty/price"}

            cost_basis = entry_price * Decimal(str(filled_qty))
            pnl = Decimal(str(filled_price)) * Decimal(str(filled_qty)) - cost_basis
            pnl_pct = float(pnl / cost_basis * 100) if cost_basis > 0 else 0
            slippage_bps = ((filled_price - current_price_ref) / current_price_ref * 10000) if current_price_ref > 0 else 0

            self.think(f"[{bot_id}] FILLED {pair} SELL: {filled_qty} @ ${filled_price:,.2f} PnL=${float(pnl):+,.2f} ({pnl_pct:+.1f}%)")

            await self._record_trade(
                pair=pair, side="SELL", qty=Decimal(str(filled_qty)),
                price=Decimal(str(filled_price)), confidence=confidence,
                reasoning=reasoning, slippage_bps=slippage_bps,
                order_id=order_result["order_id"], bot_id=bot_id,
                pnl=pnl, pnl_pct=pnl_pct,
            )

            await self._update_position(
                pair, Decimal(str(filled_qty)), Decimal(str(filled_price)),
                is_buy=False, bot_id=bot_id,
            )

            await record_realized_pnl(pair=pair, pnl=float(pnl), pnl_pct=pnl_pct, side="SELL")

            await self._telegram.trade_alert(
                pair=pair, side=f"SELL [{bot_id}]", qty=filled_qty,
                price=filled_price, confidence=confidence,
                reasoning=f"PnL: ${float(pnl):+,.2f} ({pnl_pct:+.1f}%) | {reasoning}",
            )

            return {
                "status": "filled", "pair": pair, "side": "SELL",
                "qty": filled_qty, "price": filled_price,
                "pnl": float(pnl), "pnl_pct": pnl_pct,
                "order_id": order_result["order_id"], "bot_id": bot_id,
            }

        except Exception as e:
            logger.exception("sell_failed", pair=pair, bot=bot_id, error=str(e))
            self.think(f"[{bot_id}] SELL FAILED {pair}: {e}")
            await self._telegram.error_alert("Sell Failed", f"[{bot_id}] {pair}: {e}")
            return {"status": "error", "pair": pair, "error": str(e)}

    async def _record_trade(self, **kwargs) -> None:
        side = kwargs["side"]
        is_open = side == "BUY"
        async with async_session_factory() as session:
            trade = CryptoTrade(
                bot_id=kwargs.get("bot_id", "swing"),
                pair=kwargs["pair"],
                side=side,
                qty=kwargs["qty"],
                entry_price=kwargs["price"] if is_open else Decimal(0),
                exit_price=kwargs["price"] if not is_open else None,
                pnl=kwargs.get("pnl"),
                pnl_pct=kwargs.get("pnl_pct"),
                slippage_bps=kwargs.get("slippage_bps"),
                confidence=kwargs.get("confidence"),
                reasoning=kwargs.get("reasoning"),
                target_price=kwargs.get("target_price"),
                stop_price=kwargs.get("stop_price"),
                status="open" if is_open else "closed",
                exchange_order_id=kwargs.get("order_id"),
            )
            session.add(trade)
            await session.commit()

    async def _update_position(
        self, pair: str, qty: Decimal, price: Decimal,
        is_buy: bool, bot_id: str = "swing",
    ) -> None:
        async with async_session_factory() as session:
            stmt = select(CryptoPosition).where(
                and_(CryptoPosition.pair == pair, CryptoPosition.bot_id == bot_id)
            )
            result = await session.execute(stmt)
            position = result.scalar_one_or_none()

            if is_buy:
                if position:
                    old_value = position.qty * position.avg_entry_price
                    new_value = qty * price
                    total_qty = position.qty + qty
                    position.avg_entry_price = (old_value + new_value) / total_qty if total_qty > 0 else price
                    position.qty = total_qty
                    position.current_price = price
                else:
                    position = CryptoPosition(
                        bot_id=bot_id, pair=pair, side="long", qty=qty,
                        avg_entry_price=price, current_price=price,
                    )
                    session.add(position)
            else:
                if position:
                    position.qty -= qty
                    if position.qty <= 0:
                        await session.delete(position)
                    else:
                        position.current_price = price

            await session.commit()

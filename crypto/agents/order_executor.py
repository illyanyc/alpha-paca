"""OrderExecutorAgent — submits orders to Coinbase, monitors fills, records to DB."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from agents.base import BaseAgent
from db.engine import async_session_factory
from db.models import CryptoPosition, CryptoTrade
from services.coinbase_crypto import CoinbaseCryptoService
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
            decision: dict with action, pair, size_pct, confidence, reasoning
            price: current mid price for the pair
            available_capital: float
        """
        decision = kwargs.get("decision", {})
        price = kwargs.get("price", 0)
        available_capital = kwargs.get("available_capital", 0)

        action = decision.get("action", "HOLD")
        pair = decision.get("pair", "")
        size_pct = decision.get("size_pct", 0)
        confidence = decision.get("confidence", 0)
        reasoning = decision.get("reasoning", "")

        if action == "HOLD":
            return {"status": "no_action", "pair": pair}

        if action == "BUY":
            notional = available_capital * (size_pct / 100)
            self.think(f"⚡ Executing BUY {pair}: ${notional:,.0f} at ${price:,.2f} (conf={confidence:.2f})")
            return await self._execute_buy(pair, size_pct, price, available_capital, confidence, reasoning)
        elif action == "SELL":
            self.think(f"⚡ Executing SELL {pair} (conf={confidence:.2f})")
            return await self._execute_sell(pair, confidence, reasoning)

        return {"status": "unknown_action", "action": action}

    async def _execute_buy(
        self,
        pair: str,
        size_pct: float,
        price: float,
        available_capital: float,
        confidence: float,
        reasoning: str,
    ) -> dict:
        notional = available_capital * (size_pct / 100)
        qty = Decimal(str(notional / price)) if price > 0 else Decimal(0)

        if qty <= 0:
            return {"status": "skip", "reason": "zero qty"}

        try:
            logger.info("submitting_buy", pair=pair, qty=str(qty), notional=notional)
            order_result = await asyncio.to_thread(self._exchange.submit_market_order, pair, qty, "BUY")
            self.think(f"📤 Order submitted {pair} BUY — id={order_result['order_id'][:8]}... waiting for fill")
            fill = await self._exchange.wait_for_fill(order_result["order_id"])

            filled_price = fill.get("filled_avg_price", price)
            filled_qty = fill.get("filled_qty", float(qty))
            fill_status = fill.get("status", "unknown")
            slippage_bps = ((filled_price - price) / price * 10000) if price > 0 else 0

            self.think(f"✅ FILLED {pair} BUY: {filled_qty} @ ${filled_price:,.2f} (status={fill_status}, slip={slippage_bps:.1f}bps)")

            await self._record_trade(
                pair=pair,
                side="BUY",
                qty=Decimal(str(filled_qty)),
                price=Decimal(str(filled_price)),
                confidence=confidence,
                reasoning=reasoning,
                slippage_bps=slippage_bps,
                order_id=order_result["order_id"],
            )

            await self._update_position(pair, Decimal(str(filled_qty)), Decimal(str(filled_price)), is_buy=True)

            await self._telegram.trade_alert(
                pair=pair, side="BUY", qty=filled_qty,
                price=filled_price, confidence=confidence, reasoning=reasoning,
            )

            logger.info(
                "buy_executed", pair=pair, qty=filled_qty,
                price=filled_price, slippage_bps=slippage_bps,
            )
            return {
                "status": "filled",
                "pair": pair,
                "side": "BUY",
                "qty": filled_qty,
                "price": filled_price,
                "slippage_bps": slippage_bps,
                "order_id": order_result["order_id"],
            }

        except Exception as e:
            logger.exception("buy_failed", pair=pair, error=str(e))
            self.think(f"❌ BUY FAILED {pair}: {e}")
            await self._telegram.error_alert("Buy Failed", f"{pair}: {e}")
            return {"status": "error", "pair": pair, "error": str(e)}

    async def _execute_sell(self, pair: str, confidence: float, reasoning: str) -> dict:
        """Sell entire position (exit long, go to cash).

        Resolves the sellable quantity from (1) DB position, then (2) exchange
        holdings as fallback, so sells work even if the DB drifted.
        """
        try:
            qty: Decimal = Decimal(0)
            entry_price: Decimal = Decimal(0)
            current_price_ref: float = 0.0

            async with async_session_factory() as session:
                stmt = select(CryptoPosition).where(CryptoPosition.pair == pair)
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
                self.think(f"⏭️ {pair} SELL skipped — no position found on DB or exchange")
                return {"status": "skip", "reason": "no position to sell"}

            logger.info("submitting_sell", pair=pair, qty=str(qty))
            order_result = await asyncio.to_thread(self._exchange.submit_market_order, pair, qty, "SELL")
            self.think(f"📤 Order submitted {pair} SELL — id={order_result['order_id'][:8]}... waiting for fill")
            fill = await self._exchange.wait_for_fill(order_result["order_id"])

            filled_price = fill.get("filled_avg_price", 0)
            filled_qty = fill.get("filled_qty", float(qty))
            fill_status = fill.get("status", "unknown")
            self.think(f"✅ FILLED {pair} SELL: {filled_qty} @ ${filled_price:,.2f} (status={fill_status})")

            cost_basis = entry_price * qty
            pnl = Decimal(str(filled_price)) * Decimal(str(filled_qty)) - cost_basis
            pnl_pct = float(pnl / cost_basis * 100) if cost_basis > 0 else 0
            slippage_bps = ((filled_price - current_price_ref) / current_price_ref * 10000) if current_price_ref > 0 else 0

            await self._record_trade(
                pair=pair,
                side="SELL",
                qty=Decimal(str(filled_qty)),
                price=Decimal(str(filled_price)),
                confidence=confidence,
                reasoning=reasoning,
                slippage_bps=slippage_bps,
                order_id=order_result["order_id"],
                pnl=pnl,
                pnl_pct=pnl_pct,
            )

            await self._update_position(pair, Decimal(str(filled_qty)), Decimal(str(filled_price)), is_buy=False)

            await self._telegram.trade_alert(
                pair=pair, side="SELL", qty=filled_qty,
                price=filled_price, confidence=confidence,
                reasoning=f"P&L: ${float(pnl):+,.2f} ({pnl_pct:+.1f}%) | {reasoning}",
            )

            logger.info("sell_executed", pair=pair, qty=filled_qty, price=filled_price, pnl=float(pnl))
            return {
                "status": "filled", "pair": pair, "side": "SELL",
                "qty": filled_qty, "price": filled_price,
                "pnl": float(pnl), "pnl_pct": pnl_pct,
                "order_id": order_result["order_id"],
            }

        except Exception as e:
            logger.exception("sell_failed", pair=pair, error=str(e))
            self.think(f"❌ SELL FAILED {pair}: {e}")
            await self._telegram.error_alert("Sell Failed", f"{pair}: {e}")
            return {"status": "error", "pair": pair, "error": str(e)}

    async def _record_trade(self, **kwargs) -> None:
        async with async_session_factory() as session:
            trade = CryptoTrade(
                pair=kwargs["pair"],
                side=kwargs["side"],
                qty=kwargs["qty"],
                entry_price=kwargs["price"] if kwargs["side"] == "BUY" else Decimal(0),
                exit_price=kwargs["price"] if kwargs["side"] == "SELL" else None,
                pnl=kwargs.get("pnl"),
                pnl_pct=kwargs.get("pnl_pct"),
                slippage_bps=kwargs.get("slippage_bps"),
                confidence=kwargs.get("confidence"),
                reasoning=kwargs.get("reasoning"),
                status="open" if kwargs["side"] == "BUY" else "closed",
                alpaca_order_id=kwargs.get("order_id"),
            )
            session.add(trade)
            await session.commit()

    async def _update_position(
        self, pair: str, qty: Decimal, price: Decimal, is_buy: bool
    ) -> None:
        async with async_session_factory() as session:
            stmt = select(CryptoPosition).where(CryptoPosition.pair == pair)
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
                        pair=pair, qty=qty, avg_entry_price=price, current_price=price,
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

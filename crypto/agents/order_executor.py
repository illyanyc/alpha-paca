"""OrderExecutorAgent — submits orders to Alpaca, monitors fills, records to DB."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from agents.base import BaseAgent
from db.engine import async_session_factory
from db.models import CryptoPosition, CryptoTrade
from services.alpaca_crypto import AlpacaCryptoService
from services.telegram import TelegramService

logger = structlog.get_logger(__name__)


class OrderExecutorAgent(BaseAgent):
    name = "order_executor"

    def __init__(self, alpaca: AlpacaCryptoService, telegram: TelegramService) -> None:
        super().__init__()
        self._alpaca = alpaca
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
            return await self._execute_buy(pair, size_pct, price, available_capital, confidence, reasoning)
        elif action == "SELL":
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
            order_result = self._alpaca.submit_market_order(pair, qty, "BUY")
            fill = await self._alpaca.wait_for_fill(order_result["order_id"])

            filled_price = fill.get("filled_avg_price", price)
            filled_qty = fill.get("filled_qty", float(qty))
            slippage_bps = ((filled_price - price) / price * 10000) if price > 0 else 0

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
            logger.exception("buy_failed", pair=pair)
            await self._telegram.error_alert("Buy Failed", f"{pair}: {e}")
            return {"status": "error", "pair": pair, "error": str(e)}

    async def _execute_sell(self, pair: str, confidence: float, reasoning: str) -> dict:
        """Sell entire position (go to cash)."""
        try:
            async with async_session_factory() as session:
                stmt = select(CryptoPosition).where(CryptoPosition.pair == pair)
                result = await session.execute(stmt)
                position = result.scalar_one_or_none()

            if not position or position.qty <= 0:
                return {"status": "skip", "reason": "no position to sell"}

            qty = position.qty
            entry_price = position.avg_entry_price

            order_result = self._alpaca.submit_market_order(pair, qty, "SELL")
            fill = await self._alpaca.wait_for_fill(order_result["order_id"])

            filled_price = fill.get("filled_avg_price", 0)
            filled_qty = fill.get("filled_qty", float(qty))

            pnl = Decimal(str(filled_price)) * Decimal(str(filled_qty)) - entry_price * qty
            pnl_pct = float(pnl / (entry_price * qty) * 100) if entry_price * qty > 0 else 0
            slippage_bps = ((filled_price - float(position.current_price)) / float(position.current_price) * 10000) if position.current_price > 0 else 0

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
            logger.exception("sell_failed", pair=pair)
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

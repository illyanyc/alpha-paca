"""Telegram bot service for trade alerts, reports, and error notifications."""

from __future__ import annotations

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from config import get_settings

logger = structlog.get_logger(__name__)


class TelegramService:
    """Async Telegram notification service."""

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.telegram.bot_token
        self._chat_id = settings.telegram.chat_id
        self._bot: Bot | None = None
        self._enabled = bool(self._token and self._chat_id)
        if not self._enabled:
            logger.warning("telegram_disabled", reason="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=self._token)
        return self._bot

    async def send(self, text: str, parse_mode: str = ParseMode.MARKDOWN) -> None:
        if not self._enabled:
            logger.debug("telegram_skipped", text=text[:80])
            return
        try:
            bot = self._get_bot()
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            logger.info("telegram_sent", chars=len(text))
        except Exception:
            logger.exception("telegram_send_failed")

    # ── Convenience formatters ───────────────────────────────────────

    async def trade_alert(
        self,
        pair: str,
        side: str,
        qty: float,
        price: float,
        confidence: float,
        reasoning: str,
    ) -> None:
        icon = "🟢" if side.upper() == "BUY" else "🔴"
        msg = (
            f"{icon} *{side.upper()} {pair}*\n"
            f"Qty: `{qty:.6f}` @ `${price:,.2f}`\n"
            f"Confidence: `{confidence:.0%}`\n"
            f"Reason: _{reasoning[:200]}_"
        )
        await self.send(msg)

    async def hourly_summary(
        self,
        positions: list[dict],
        unrealized_pnl: float,
        exposure_pct: float,
    ) -> None:
        lines = ["📊 *Hourly Crypto Summary*\n"]
        for p in positions:
            lines.append(
                f"• {p['pair']}: `{p['qty']:.6f}` @ `${p['current_price']:,.2f}` "
                f"(PnL: `${p['unrealized_pnl']:+,.2f}`)"
            )
        lines.append(f"\nTotal Unrealized PnL: `${unrealized_pnl:+,.2f}`")
        lines.append(f"Exposure: `{exposure_pct:.1f}%`")
        await self.send("\n".join(lines))

    async def daily_report(
        self,
        total_pnl: float,
        win_rate: float,
        trade_count: int,
        best_trade: str,
        worst_trade: str,
    ) -> None:
        msg = (
            "📈 *Daily Crypto Report*\n\n"
            f"Total P&L: `${total_pnl:+,.2f}`\n"
            f"Win Rate: `{win_rate:.1%}`\n"
            f"Trades: `{trade_count}`\n"
            f"Best: _{best_trade}_\n"
            f"Worst: _{worst_trade}_"
        )
        await self.send(msg)

    async def error_alert(self, title: str, details: str) -> None:
        msg = f"🚨 *{title}*\n\n```\n{details[:500]}\n```"
        await self.send(msg)

    async def service_restart(self, attempt: int, reason: str) -> None:
        msg = (
            f"🔄 *Service Restart* (attempt #{attempt})\n"
            f"Reason: _{reason}_"
        )
        await self.send(msg)

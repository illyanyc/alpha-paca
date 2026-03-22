"""RiskGuard — shared account-level risk engine for both SwingSniper and DaySniper.

Enforces: daily loss halt, max drawdown breaker, per-trade risk caps, R/R gates,
position count limits, anti-churn intervals, and consecutive-loss cooldowns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)


class RiskVerdict:
    __slots__ = ("approved", "reason")

    def __init__(self, approved: bool, reason: str = "") -> None:
        self.approved = approved
        self.reason = reason


class RiskGuard:
    """Stateful, account-level risk gatekeeper shared by both bots."""

    def __init__(self) -> None:
        self._last_trade_times: dict[str, datetime] = {}  # key = f"{bot_id}:{pair}"
        self._consecutive_losses: dict[str, int] = {}  # key = bot_id
        self._halted_bots: dict[str, datetime | None] = {}  # bot_id -> halt expiry
        self._daily_halt: bool = False

    def record_trade_time(self, bot_id: str, pair: str) -> None:
        self._last_trade_times[f"{bot_id}:{pair}"] = datetime.now(timezone.utc)

    def record_loss(self, bot_id: str) -> None:
        self._consecutive_losses[bot_id] = self._consecutive_losses.get(bot_id, 0) + 1

    def record_win(self, bot_id: str) -> None:
        self._consecutive_losses[bot_id] = 0

    def reset_daily_halt(self) -> None:
        self._daily_halt = False

    def force_daily_halt(self) -> None:
        self._daily_halt = True

    def check(
        self,
        bot_id: str,
        decision: dict[str, Any],
        positions: list[dict],
        portfolio: dict[str, Any],
    ) -> RiskVerdict:
        """Run all risk checks. Returns approved=True only if ALL pass."""
        action = decision.get("action", "")

        if action in ("SELL",):
            return RiskVerdict(True)

        checks = [
            self._check_daily_halt(),
            self._check_bot_halt(bot_id),
            self._check_drawdown(portfolio),
            self._check_daily_loss(portfolio),
            self._check_exposure(portfolio),
            self._check_concurrent_positions(bot_id, positions),
            self._check_per_trade_risk(decision),
            self._check_rr_ratio(bot_id, decision),
            self._check_anti_churn(bot_id, decision),
            self._check_consecutive_loss_cooldown(bot_id),
        ]

        failures = [c for c in checks if not c.approved]
        if failures:
            reasons = "; ".join(f.reason for f in failures)
            logger.info("risk_rejected", bot=bot_id, pair=decision.get("pair"), reasons=reasons)
            return RiskVerdict(False, reasons)

        return RiskVerdict(True)

    def _check_daily_halt(self) -> RiskVerdict:
        if self._daily_halt:
            return RiskVerdict(False, "Daily loss halt active — no new trades")
        return RiskVerdict(True)

    def _check_bot_halt(self, bot_id: str) -> RiskVerdict:
        expiry = self._halted_bots.get(bot_id)
        if expiry and datetime.now(timezone.utc) < expiry:
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            return RiskVerdict(False, f"{bot_id} halted for {remaining:.0f}s (consecutive losses)")
        if expiry:
            del self._halted_bots[bot_id]
        return RiskVerdict(True)

    def _check_drawdown(self, portfolio: dict) -> RiskVerdict:
        settings = get_settings()
        dd = portfolio.get("drawdown_pct", 0)
        if dd >= settings.crypto.max_drawdown_pct:
            return RiskVerdict(False, f"Drawdown {dd:.1f}% >= {settings.crypto.max_drawdown_pct}% — circuit breaker")
        return RiskVerdict(True)

    def _check_daily_loss(self, portfolio: dict) -> RiskVerdict:
        settings = get_settings()
        nav = portfolio.get("nav", 0)
        daily_pnl = portfolio.get("realized_pnl_today", 0)
        if nav > 0 and daily_pnl < 0:
            loss_pct = abs(daily_pnl) / nav * 100
            if loss_pct >= settings.crypto.daily_loss_halt_pct:
                self._daily_halt = True
                return RiskVerdict(False, f"Daily loss {loss_pct:.1f}% >= {settings.crypto.daily_loss_halt_pct}% — halting")
        return RiskVerdict(True)

    def _check_exposure(self, portfolio: dict) -> RiskVerdict:
        exposure = portfolio.get("total_exposure_pct", 0)
        if exposure >= 100:
            return RiskVerdict(False, f"Total exposure {exposure:.1f}% >= 100% cap")
        return RiskVerdict(True)

    def _check_concurrent_positions(self, bot_id: str, positions: list[dict]) -> RiskVerdict:
        settings = get_settings()
        bot_count = sum(1 for p in positions if p.get("bot_id") == bot_id and float(p.get("qty", 0)) > 0)
        total_count = sum(1 for p in positions if float(p.get("qty", 0)) > 0)

        if bot_count >= settings.crypto.max_concurrent_per_bot:
            return RiskVerdict(False, f"{bot_id} has {bot_count} positions (max {settings.crypto.max_concurrent_per_bot})")
        if total_count >= settings.crypto.max_concurrent_total:
            return RiskVerdict(False, f"Total {total_count} positions (max {settings.crypto.max_concurrent_total})")
        return RiskVerdict(True)

    def _check_per_trade_risk(self, decision: dict) -> RiskVerdict:
        settings = get_settings()
        size_pct = decision.get("size_pct", 0)
        if size_pct > settings.crypto.max_risk_per_trade_pct:
            return RiskVerdict(False, f"Trade size {size_pct:.1f}% > max {settings.crypto.max_risk_per_trade_pct}%")
        return RiskVerdict(True)

    def _check_rr_ratio(self, bot_id: str, decision: dict) -> RiskVerdict:
        settings = get_settings()
        target = decision.get("target_price", 0)
        stop = decision.get("stop_price", 0)
        entry = decision.get("entry_price", 0)

        if not target or not stop or not entry or entry <= 0:
            return RiskVerdict(True)

        reward = abs(target - entry)
        risk = abs(entry - stop)
        if risk <= 0:
            return RiskVerdict(False, "Stop price equals entry — infinite risk")

        rr = reward / risk
        min_rr = settings.crypto.swing_min_rr_ratio if bot_id == "swing" else settings.crypto.day_min_rr_ratio

        if rr < min_rr:
            return RiskVerdict(False, f"R/R {rr:.2f} < min {min_rr} for {bot_id}")
        return RiskVerdict(True)

    def _check_anti_churn(self, bot_id: str, decision: dict) -> RiskVerdict:
        settings = get_settings()
        pair = decision.get("pair", "")
        key = f"{bot_id}:{pair}"
        min_interval = (
            settings.crypto.swing_min_trade_interval_sec
            if bot_id == "swing"
            else settings.crypto.day_min_trade_interval_sec
        )

        losses = self._consecutive_losses.get(bot_id, 0)
        if losses >= settings.crypto.cooldown_after_losses:
            min_interval *= 2

        last = self._last_trade_times.get(key)
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < min_interval:
                return RiskVerdict(False, f"{pair} traded {elapsed:.0f}s ago (min {min_interval}s for {bot_id})")
        return RiskVerdict(True)

    def _check_consecutive_loss_cooldown(self, bot_id: str) -> RiskVerdict:
        settings = get_settings()
        losses = self._consecutive_losses.get(bot_id, 0)
        if losses >= settings.crypto.cooldown_halt_after_losses:
            from datetime import timedelta
            self._halted_bots[bot_id] = datetime.now(timezone.utc) + timedelta(hours=1)
            self._consecutive_losses[bot_id] = 0
            return RiskVerdict(False, f"{bot_id} halted 1h after {losses} consecutive losses")
        return RiskVerdict(True)

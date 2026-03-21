"""RiskValidatorAgent — pre-trade risk checks (position limits, drawdown, correlation)."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from agents.base import BaseAgent
from config import get_settings

logger = structlog.get_logger(__name__)


class RiskCheckResult:
    def __init__(self, approved: bool, reason: str = ""):
        self.approved = approved
        self.reason = reason


class RiskValidatorAgent(BaseAgent):
    name = "risk_validator"

    def __init__(self) -> None:
        super().__init__()
        self._last_trade_times: dict[str, datetime] = {}

    async def run(self, **kwargs) -> dict:
        """Validate a proposed trade decision against risk rules.

        kwargs expected:
            decision: dict with action, pair, size_pct, confidence
            positions: list of current positions
            portfolio_state: dict with nav, cash, exposure, drawdown
        """
        decision = kwargs.get("decision", {})
        positions = kwargs.get("positions", [])
        portfolio = kwargs.get("portfolio_state", {})

        if decision.get("action") == "SELL":
            self.think(f"🛡️ {decision.get('pair')} SELL — auto-approved (exit)")
            return {"approved": True, "reasons": ""}

        checks = []

        checks.append(self._check_drawdown(portfolio))
        checks.append(self._check_exposure(decision, portfolio))
        checks.append(self._check_position_limit(decision, positions))
        checks.append(self._check_anti_churn(decision))
        checks.append(self._check_correlation(decision, positions))

        failures = [c for c in checks if not c.approved]

        if failures:
            reasons = "; ".join(f.reason for f in failures)
            self.think(f"🛡️ {decision.get('pair')} {decision.get('action')} REJECTED: {reasons}")
            return {"approved": False, "reasons": reasons}

        if decision.get("action") in ("BUY", "SELL"):
            self._last_trade_times[decision["pair"]] = datetime.now(timezone.utc)

        self.think(f"🛡️ {decision.get('pair')} {decision.get('action')} — approved (all 5 checks passed)")
        return {"approved": True, "reasons": ""}

    def _check_drawdown(self, portfolio: dict) -> RiskCheckResult:
        settings = get_settings()
        dd = portfolio.get("drawdown_pct", 0)
        max_dd = settings.crypto.max_drawdown_pct
        if dd >= max_dd:
            return RiskCheckResult(False, f"Drawdown {dd:.1f}% >= limit {max_dd}%")
        return RiskCheckResult(True)

    def _check_exposure(self, decision: dict, portfolio: dict) -> RiskCheckResult:
        settings = get_settings()
        if decision.get("action") != "BUY":
            return RiskCheckResult(True)
        current_exp = portfolio.get("total_exposure_pct", 0)
        new_exp = current_exp + decision.get("size_pct", 0)
        max_exp = settings.crypto.max_total_exposure_pct
        if new_exp > max_exp:
            return RiskCheckResult(
                False,
                f"Exposure would be {new_exp:.1f}% > max {max_exp}%",
            )
        return RiskCheckResult(True)

    def _check_position_limit(self, decision: dict, positions: list) -> RiskCheckResult:
        settings = get_settings()
        if decision.get("action") != "BUY":
            return RiskCheckResult(True)
        pair = decision.get("pair", "")
        size_pct = decision.get("size_pct", 0)
        max_pos = settings.crypto.max_position_pct

        existing_pct = 0
        for p in positions:
            p_pair = p.get("pair", p.get("symbol", ""))
            if p_pair == pair:
                mv = float(p.get("market_value_usd", p.get("market_value", 0)))
                nav = float(p.get("nav", settings.crypto.max_capital))
                existing_pct = (mv / nav * 100) if nav > 0 else 0

        if existing_pct + size_pct > max_pos:
            return RiskCheckResult(
                False,
                f"{pair} position would be {existing_pct + size_pct:.1f}% > max {max_pos}%",
            )
        return RiskCheckResult(True)

    def _check_anti_churn(self, decision: dict) -> RiskCheckResult:
        settings = get_settings()
        pair = decision.get("pair", "")
        min_interval = settings.crypto.min_trade_interval_sec

        last_time = self._last_trade_times.get(pair)
        if last_time:
            elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
            if elapsed < min_interval:
                return RiskCheckResult(
                    False,
                    f"{pair} traded {elapsed:.0f}s ago (min {min_interval}s)",
                )
        return RiskCheckResult(True)

    def _check_correlation(self, decision: dict, positions: list) -> RiskCheckResult:
        """Basic correlation check — limit highly correlated crypto exposure."""
        if decision.get("action") != "BUY":
            return RiskCheckResult(True)

        correlated_groups = {
            "BTC-correlated": {"BTC/USD", "ETH/USD"},
            "alt-coins": {"SOL/USD", "LINK/USD", "DOGE/USD"},
        }

        pair = decision.get("pair", "")
        my_group = None
        for group_name, members in correlated_groups.items():
            if pair in members:
                my_group = (group_name, members)
                break

        if my_group is None:
            return RiskCheckResult(True)

        group_name, members = my_group
        group_exposure = 0
        for p in positions:
            p_pair = p.get("pair", p.get("symbol", ""))
            if p_pair in members and p_pair != pair:
                mv = float(p.get("market_value_usd", p.get("market_value", 0)))
                settings = get_settings()
                nav = settings.crypto.max_capital
                group_exposure += (mv / nav * 100) if nav > 0 else 0

        max_group_pct = 60
        new_total = group_exposure + decision.get("size_pct", 0)
        if new_total > max_group_pct:
            return RiskCheckResult(
                False,
                f"{group_name} group exposure {new_total:.1f}% > max {max_group_pct}%",
            )
        return RiskCheckResult(True)

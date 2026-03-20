"""Abstract base class for all strategy pods."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class BasePod(ABC):
    """Every strategy pod extends this class and implements the three abstract hooks."""

    @abstractmethod
    def get_pod_name(self) -> str:
        """Return a unique identifier for this pod (e.g. ``'momentum'``)."""

    @abstractmethod
    def run_scan(self, universe: list[str]) -> list[dict[str, Any]]:
        """Screen the universe and return candidate symbols with scan metadata."""

    @abstractmethod
    def generate_signals(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Produce scored alpha signals from scan candidates."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def validate_signal(self, signal: dict[str, Any]) -> bool:
        """Minimum quality gate for a signal before submission."""
        score = signal.get("composite_score", 0.0)
        settings = get_settings()
        min_ic = settings.signal_qualification.min_signal_ic
        ic = signal.get("ic_weight", 0.0)
        if abs(score) < 0.01:
            logger.debug("signal_rejected_low_score", symbol=signal.get("symbol"))
            return False
        if ic < min_ic:
            logger.debug("signal_rejected_low_ic", symbol=signal.get("symbol"), ic=ic)
            return False
        return True

    def compute_position_size(
        self,
        signal: dict[str, Any],
        portfolio_nav: float,
    ) -> float:
        """Risk-per-trade position sizing (percentage of NAV).

        Uses the configured ``risk_per_trade_pct`` and clamps to ``max_position_pct``.
        """
        settings = get_settings()
        risk_pct = settings.position_sizing.risk_per_trade_pct / 100
        max_pct = settings.position_sizing.max_position_pct / 100

        entry = signal.get("entry_price", 0.0)
        stop = signal.get("stop_loss", 0.0)
        if entry == 0 or stop == 0:
            return 0.0

        risk_per_share = abs(entry - stop)
        if risk_per_share == 0:
            return 0.0

        dollar_risk = portfolio_nav * risk_pct
        shares = dollar_risk / risk_per_share
        position_value = shares * entry
        position_pct = position_value / portfolio_nav if portfolio_nav else 0.0

        return min(position_pct, max_pct) * 100

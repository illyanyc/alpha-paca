"""Microstructure signal engine — computes order flow and book imbalance signals."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MicrostructureState:
    """Snapshot of microstructure signals for a single pair."""
    pair: str
    bid_ask_imbalance: float = 0.0
    spread_bps: float = 0.0
    trade_flow_imbalance: float = 0.0
    vpin: float = 0.0
    signal: str = "neutral"
    score: float = 0.0
    confidence: float = 0.0


class OrderBookTracker:
    """Tracks order book state and computes microstructure signals."""

    def __init__(self, pair: str, depth_levels: int = 10) -> None:
        self.pair = pair
        self.depth_levels = depth_levels
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self._trade_buys: deque[tuple[float, float]] = deque(maxlen=500)
        self._trade_sells: deque[tuple[float, float]] = deque(maxlen=500)
        self._spread_history: deque[float] = deque(maxlen=120)
        self._last_update: float = 0

    def update_book(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> None:
        self.bids = sorted(bids, key=lambda x: -x[0])[:self.depth_levels]
        self.asks = sorted(asks, key=lambda x: x[0])[:self.depth_levels]
        self._last_update = time.time()

        if self.bids and self.asks:
            spread = (self.asks[0][0] - self.bids[0][0]) / self.asks[0][0] * 10000
            self._spread_history.append(spread)

    def record_trade(self, price: float, size: float, side: str) -> None:
        ts = time.time()
        if side == "buy":
            self._trade_buys.append((ts, size * price))
        else:
            self._trade_sells.append((ts, size * price))

    def bid_ask_imbalance(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        bid_vol = sum(qty for _, qty in self.bids)
        ask_vol = sum(qty for _, qty in self.asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def spread_bps(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        mid = (self.bids[0][0] + self.asks[0][0]) / 2
        if mid <= 0:
            return 0.0
        return (self.asks[0][0] - self.bids[0][0]) / mid * 10000

    def trade_flow_imbalance(self, window_sec: float = 60.0) -> float:
        now = time.time()
        cutoff = now - window_sec
        buy_vol = sum(v for ts, v in self._trade_buys if ts >= cutoff)
        sell_vol = sum(v for ts, v in self._trade_sells if ts >= cutoff)
        total = buy_vol + sell_vol
        if total == 0:
            return 0.0
        return (buy_vol - sell_vol) / total

    def vpin(self, window_sec: float = 300.0) -> float:
        """Volume-Synchronized Probability of Informed Trading (simplified).

        High VPIN (> 0.7) suggests informed trading / imminent move.
        """
        now = time.time()
        cutoff = now - window_sec
        buy_vol = sum(v for ts, v in self._trade_buys if ts >= cutoff)
        sell_vol = sum(v for ts, v in self._trade_sells if ts >= cutoff)
        total = buy_vol + sell_vol
        if total == 0:
            return 0.5
        return abs(buy_vol - sell_vol) / total

    def spread_compression(self) -> float:
        """Ratio of current spread to average spread. < 1.0 = compression."""
        if len(self._spread_history) < 10:
            return 1.0
        avg = sum(self._spread_history) / len(self._spread_history)
        current = self._spread_history[-1] if self._spread_history else avg
        return current / avg if avg > 0 else 1.0

    def get_state(self) -> MicrostructureState:
        imbalance = self.bid_ask_imbalance()
        spread = self.spread_bps()
        flow = self.trade_flow_imbalance()
        vpin_val = self.vpin()

        score = 0.0
        reasons = []

        if imbalance > 0.3:
            score += 0.3
            reasons.append("bid_heavy")
        elif imbalance < -0.3:
            score -= 0.3
            reasons.append("ask_heavy")

        if flow > 0.3:
            score += 0.25
            reasons.append("buy_flow")
        elif flow < -0.3:
            score -= 0.25
            reasons.append("sell_flow")

        if vpin_val > 0.7:
            score *= 1.3
            reasons.append("high_vpin")

        sc = self.spread_compression()
        if sc < 0.7:
            score *= 1.2
            reasons.append("spread_compress")

        score = max(-1.0, min(1.0, score))
        confidence = min(1.0, abs(score) * 1.5 + 0.1)
        signal = "buy" if score > 0.15 else ("sell" if score < -0.15 else "neutral")

        return MicrostructureState(
            pair=self.pair,
            bid_ask_imbalance=round(imbalance, 4),
            spread_bps=round(spread, 1),
            trade_flow_imbalance=round(flow, 4),
            vpin=round(vpin_val, 4),
            signal=signal,
            score=round(score, 4),
            confidence=round(confidence, 4),
        )


class MicrostructureEngine:
    """Manages order book trackers for multiple pairs."""

    def __init__(self) -> None:
        self._trackers: dict[str, OrderBookTracker] = {}

    def get_tracker(self, pair: str) -> OrderBookTracker:
        if pair not in self._trackers:
            self._trackers[pair] = OrderBookTracker(pair)
        return self._trackers[pair]

    def get_all_states(self) -> dict[str, MicrostructureState]:
        return {pair: tracker.get_state() for pair, tracker in self._trackers.items()}

    def get_signal_dict(self, pair: str) -> dict[str, Any]:
        if pair not in self._trackers:
            return {"signal": "neutral", "score": 0, "confidence": 0, "source": "microstructure"}
        state = self._trackers[pair].get_state()
        return {
            "signal": state.signal,
            "score": state.score,
            "confidence": state.confidence,
            "imbalance": state.bid_ask_imbalance,
            "flow": state.trade_flow_imbalance,
            "vpin": state.vpin,
            "spread_bps": state.spread_bps,
            "source": "microstructure",
        }

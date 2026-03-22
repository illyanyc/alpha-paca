"""Tests for microstructure signal engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.microstructure import MicrostructureEngine, MicrostructureState, OrderBookTracker


class TestOrderBookTracker:
    def test_empty_book_imbalance_zero(self):
        tracker = OrderBookTracker("BTC/USD")
        assert tracker.bid_ask_imbalance() == 0.0

    def test_bid_heavy_imbalance(self):
        tracker = OrderBookTracker("BTC/USD")
        tracker.update_book(
            bids=[(100, 10), (99, 8)],
            asks=[(101, 2), (102, 1)],
        )
        imb = tracker.bid_ask_imbalance()
        assert imb > 0.5

    def test_ask_heavy_imbalance(self):
        tracker = OrderBookTracker("BTC/USD")
        tracker.update_book(
            bids=[(100, 1)],
            asks=[(101, 10), (102, 8)],
        )
        imb = tracker.bid_ask_imbalance()
        assert imb < -0.5

    def test_spread_bps(self):
        tracker = OrderBookTracker("BTC/USD")
        tracker.update_book(
            bids=[(100, 5)],
            asks=[(101, 5)],
        )
        spread = tracker.spread_bps()
        assert spread > 0
        assert spread < 200

    def test_trade_flow_empty(self):
        tracker = OrderBookTracker("BTC/USD")
        assert tracker.trade_flow_imbalance() == 0.0

    def test_trade_flow_buy_heavy(self):
        tracker = OrderBookTracker("BTC/USD")
        for _ in range(10):
            tracker.record_trade(100, 1.0, "buy")
        tracker.record_trade(100, 0.5, "sell")
        flow = tracker.trade_flow_imbalance()
        assert flow > 0.5

    def test_vpin_no_trades(self):
        tracker = OrderBookTracker("BTC/USD")
        vpin = tracker.vpin()
        assert vpin == 0.5

    def test_get_state(self):
        tracker = OrderBookTracker("BTC/USD")
        tracker.update_book(
            bids=[(100, 5)],
            asks=[(101, 5)],
        )
        state = tracker.get_state()
        assert isinstance(state, MicrostructureState)
        assert state.pair == "BTC/USD"
        assert isinstance(state.signal, str)


class TestMicrostructureEngine:
    def test_get_tracker(self):
        engine = MicrostructureEngine()
        t = engine.get_tracker("BTC/USD")
        assert isinstance(t, OrderBookTracker)
        assert t.pair == "BTC/USD"

    def test_same_tracker_returned(self):
        engine = MicrostructureEngine()
        t1 = engine.get_tracker("BTC/USD")
        t2 = engine.get_tracker("BTC/USD")
        assert t1 is t2

    def test_get_all_states(self):
        engine = MicrostructureEngine()
        engine.get_tracker("BTC/USD")
        engine.get_tracker("ETH/USD")
        states = engine.get_all_states()
        assert "BTC/USD" in states
        assert "ETH/USD" in states

    def test_get_signal_dict(self):
        engine = MicrostructureEngine()
        sig = engine.get_signal_dict("BTC/USD")
        assert sig["signal"] == "neutral"
        assert sig["source"] == "microstructure"

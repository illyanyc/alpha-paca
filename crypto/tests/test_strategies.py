"""Tests for institutional-grade trading strategies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine.strategies import (
    ALL_STRATEGIES,
    ema_ribbon,
    funding_rate_arb,
    liquidity_grab,
    mean_reversion_zscore,
    momentum_cascade,
    order_flow_momentum,
    run_all_strategies,
    vwap_reversion,
    volatility_breakout,
)


def _make_bars(prices: list[float], base_vol: float = 1000) -> list[dict]:
    bars = []
    for i, p in enumerate(prices):
        bars.append({
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
            "open": p * 0.999,
            "high": p * 1.002,
            "low": p * 0.998,
            "close": p,
            "volume": base_vol * (1 + (i % 3) * 0.5),
            "vwap": p * 1.0005,
        })
    return bars


def _make_indicators(**overrides) -> dict:
    defaults = {
        "rsi": 50, "macd_hist": 0.01, "close": 100, "high": 101, "low": 99,
        "bb_upper": 110, "bb_lower": 90, "bb_middle": 100,
        "vwap": 100, "volume": 1000, "volume_sma": 800,
        "williams_r": -50, "ema_8": 101.5, "ema_9": 101, "ema_13": 100.5,
        "ema_21": 100, "ema_34": 99.5, "ema_55": 99,
        "momentum_5": 0.01, "momentum_10": 0.015, "momentum_20": 0.02,
        "volume_momentum": 0.1,
        "atr": 2.0, "atr_sma_20": 1.5,
        "kc_upper": 108, "kc_middle": 100, "kc_lower": 92,
    }
    defaults.update(overrides)
    return defaults


class TestVolatilityBreakout:
    def test_insufficient_bars_neutral(self):
        bars = _make_bars([100] * 5)
        sig = volatility_breakout(bars, _make_indicators())
        assert sig["signal"] == "neutral"

    def test_breakout_above_kc(self):
        sig = volatility_breakout(
            _make_bars([100] * 30),
            _make_indicators(close=110, kc_upper=108, atr=3, atr_sma_20=1.5),
        )
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.3

    def test_breakdown_below_kc(self):
        sig = volatility_breakout(
            _make_bars([100] * 30),
            _make_indicators(close=88, kc_lower=92, atr=3, atr_sma_20=1.5),
        )
        assert sig["signal"] == "sell"
        assert sig["score"] < -0.3


class TestMeanReversionZscore:
    def test_insufficient_bars_neutral(self):
        bars = _make_bars([100] * 10)
        sig = mean_reversion_zscore(bars, _make_indicators())
        assert sig["signal"] == "neutral"

    def test_extreme_oversold(self):
        prices = [100] * 55 + [85]
        sig = mean_reversion_zscore(_make_bars(prices), _make_indicators(rsi=25, williams_r=-92))
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.4

    def test_extreme_overbought(self):
        prices = [100] * 55 + [115]
        sig = mean_reversion_zscore(_make_bars(prices), _make_indicators(rsi=78))
        assert sig["signal"] == "sell"
        assert sig["score"] < -0.3


class TestMomentumCascade:
    def test_insufficient_bars_neutral(self):
        sig = momentum_cascade(_make_bars([100] * 5), _make_indicators())
        assert sig["signal"] == "neutral"

    def test_all_bull_factors(self):
        sig = momentum_cascade(
            _make_bars([100] * 30),
            _make_indicators(momentum_5=0.02, momentum_10=0.03, momentum_20=0.05, volume_momentum=0.5, rsi=60),
        )
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.5


class TestLiquidityGrab:
    def test_insufficient_bars_neutral(self):
        sig = liquidity_grab(_make_bars([100] * 5), _make_indicators())
        assert sig["signal"] == "neutral"


class TestVwapReversion:
    def test_insufficient_bars_neutral(self):
        sig = vwap_reversion(_make_bars([100] * 5), _make_indicators())
        assert sig["signal"] == "neutral"


class TestEmaRibbon:
    def test_insufficient_bars_neutral(self):
        sig = ema_ribbon(_make_bars([100] * 20), _make_indicators())
        assert sig["signal"] == "neutral"

    def test_bullish_alignment(self):
        sig = ema_ribbon(
            _make_bars([100] * 65),
            _make_indicators(ema_8=105, ema_13=104, ema_21=103, ema_34=102, ema_55=101, macd_hist=0.1),
        )
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.3


class TestOrderFlowMomentum:
    def test_no_micro_data_neutral(self):
        sig = order_flow_momentum(_make_bars([100] * 30), _make_indicators())
        assert sig["signal"] == "neutral"

    def test_strong_buy_flow(self):
        sig = order_flow_momentum(
            _make_bars([100] * 30), _make_indicators(),
            microstructure={"imbalance": 0.5, "flow": 0.5, "vpin": 0.8},
        )
        assert sig["signal"] == "buy"
        assert sig["score"] > 0.5


class TestFundingRateArb:
    def test_no_funding_neutral(self):
        sig = funding_rate_arb(_make_bars([100] * 30), _make_indicators())
        assert sig["signal"] == "neutral"

    def test_extreme_positive_funding(self):
        sig = funding_rate_arb(
            _make_bars([100] * 30), _make_indicators(),
            onchain={"btc_funding": 0.001},
        )
        assert sig["signal"] == "sell"

    def test_extreme_negative_funding(self):
        sig = funding_rate_arb(
            _make_bars([100] * 30), _make_indicators(),
            onchain={"btc_funding": -0.001},
        )
        assert sig["signal"] == "buy"


class TestRunAllStrategies:
    def test_returns_all_eight(self):
        bars = _make_bars(list(range(90, 160)))
        results = run_all_strategies(bars, _make_indicators())
        assert len(results) == 8
        names = {r["name"] for r in results}
        assert names == set(ALL_STRATEGIES.keys())

    def test_all_have_required_keys(self):
        bars = _make_bars([100] * 60)
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert "signal" in r
            assert "score" in r
            assert "confidence" in r
            assert "name" in r

    def test_scores_bounded(self):
        bars = _make_bars(list(range(80, 150)))
        results = run_all_strategies(bars, _make_indicators())
        for r in results:
            assert -1.0 <= r["score"] <= 1.0
            assert 0 <= r["confidence"] <= 1.0


class TestAllStrategiesRegistry:
    def test_registry_has_eight_entries(self):
        assert len(ALL_STRATEGIES) == 8

    def test_all_callables(self):
        for name, fn in ALL_STRATEGIES.items():
            assert callable(fn), f"{name} is not callable"

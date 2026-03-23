"""Tests for technical indicators."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.indicators import rsi, macd, bollinger_bands, vwap, atr, volume_sma, sma, compute_all


def _make_closes(n: int = 100, start: float = 100.0) -> pd.Series:
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, n)
    prices = start * np.cumprod(1 + returns)
    return pd.Series(prices)


def _make_bars(n: int = 100) -> list[dict]:
    np.random.seed(42)
    bars = []
    price = 70000.0
    for i in range(n):
        change = np.random.normal(0, 200)
        o = price
        c = price + change
        h = max(o, c) + abs(np.random.normal(0, 100))
        lo = min(o, c) - abs(np.random.normal(0, 100))
        v = abs(np.random.normal(1000, 500))
        bars.append({"open": o, "high": h, "low": lo, "close": c, "volume": v, "timestamp": f"2026-01-01T{i:02d}:00:00Z"})
        price = c
    return bars


class TestRSI:
    def test_rsi_range(self):
        closes = _make_closes(100)
        result = rsi(closes)
        valid = result.dropna()
        assert all(0 <= v <= 100 for v in valid)

    def test_rsi_oversold_on_decline(self):
        closes = pd.Series([100 - i * 2 for i in range(30)])
        result = rsi(closes, period=14)
        assert result.iloc[-1] < 30


class TestMACD:
    def test_returns_three_series(self):
        closes = _make_closes(50)
        ml, sl, hist = macd(closes)
        assert len(ml) == len(closes)
        assert len(sl) == len(closes)
        assert len(hist) == len(closes)

    def test_histogram_is_difference(self):
        closes = _make_closes(50)
        ml, sl, hist = macd(closes)
        diff = (ml - sl).dropna()
        h = hist.dropna()
        assert np.allclose(diff.values[-10:], h.values[-10:], atol=1e-10)


class TestBollingerBands:
    def test_upper_above_lower(self):
        closes = _make_closes(50)
        upper, middle, lower = bollinger_bands(closes)
        valid_idx = upper.dropna().index
        assert all(upper[i] >= lower[i] for i in valid_idx)

    def test_middle_is_sma(self):
        closes = _make_closes(50)
        _, middle, _ = bollinger_bands(closes, period=20)
        sma = closes.rolling(20).mean()
        valid = middle.dropna()
        assert np.allclose(valid.values, sma.dropna().values, atol=1e-10)


class TestATR:
    def test_atr_positive(self):
        bars = _make_bars(50)
        df = pd.DataFrame(bars)
        result = atr(df["high"], df["low"], df["close"])
        valid = result.dropna()
        assert all(v > 0 for v in valid)


class TestVWAP:
    def test_vwap_in_range(self):
        bars = _make_bars(50)
        df = pd.DataFrame(bars)
        result = vwap(df["high"], df["low"], df["close"], df["volume"])
        valid = result.dropna()
        assert all(df["low"].min() <= v <= df["high"].max() for v in valid)


class TestSMA:
    def test_sma_correct_value(self):
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(closes, 3)
        assert abs(result.iloc[-1] - 4.0) < 0.01  # (3+4+5)/3


class TestComputeAll:
    def test_full_computation(self):
        bars = _make_bars(100)
        result = compute_all(bars)
        assert result["rsi"] is not None
        assert result["rsi_5"] is not None
        assert result["macd_line"] is not None
        assert result["macd_4h_line"] is not None
        assert result["macd_4h_signal"] is not None
        assert result["macd_4h_hist"] is not None
        assert result["bb_upper"] is not None
        assert result["atr"] is not None
        assert result["close"] is not None
        assert result["vol_ratio_20"] is not None
        assert result["sma_200"] is not None
        assert isinstance(result["macd_4h_bullish_cross"], bool)
        assert isinstance(result["macd_4h_bearish_cross"], bool)

    def test_insufficient_data(self):
        bars = _make_bars(10)
        result = compute_all(bars)
        assert result["rsi"] is None
        assert result["rsi_5"] is None
        assert result["vol_ratio_20"] is None

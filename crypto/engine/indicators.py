"""Technical indicators for crypto price analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Upper band, middle band (SMA), lower band."""
    middle = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def vwap(highs: pd.Series, lows: pd.Series, closes: pd.Series, volumes: pd.Series) -> pd.Series:
    """Volume Weighted Average Price (cumulative within the window)."""
    typical_price = (highs + lows + closes) / 3
    cumulative_tp_vol = (typical_price * volumes).cumsum()
    cumulative_vol = volumes.cumsum()
    return cumulative_tp_vol / cumulative_vol.replace(0, np.nan)


def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = closes.shift(1)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period).mean()


def volume_sma(volumes: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return volumes.rolling(window=period).mean()


def williams_r(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Williams %R oscillator (-100 to 0)."""
    highest = highs.rolling(window=period).max()
    lowest = lows.rolling(window=period).min()
    wr = -100 * (highest - closes) / (highest - lowest).replace(0, np.nan)
    return wr


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_all(bars: list[dict]) -> dict[str, float | None]:
    """Compute all indicators from a list of OHLCV bar dicts.

    Returns latest values for each indicator, or None if insufficient data.
    """
    if len(bars) < 30:
        return {
            "rsi": None, "macd_line": None, "macd_signal": None, "macd_hist": None,
            "bb_upper": None, "bb_middle": None, "bb_lower": None,
            "vwap": None, "atr": None, "volume_sma": None,
            "williams_r": None, "ema_9": None, "ema_21": None,
            "momentum_5": None, "momentum_10": None,
        }

    df = pd.DataFrame(bars)
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    volumes = df["volume"].astype(float)

    rsi_val = rsi(closes)
    macd_l, macd_s, macd_h = macd(closes)
    bb_u, bb_m, bb_lo = bollinger_bands(closes)
    vwap_val = vwap(highs, lows, closes, volumes)
    atr_val = atr(highs, lows, closes)
    vol_sma_val = volume_sma(volumes)
    wr_val = williams_r(highs, lows, closes)
    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    mom_5 = closes.pct_change(5)
    mom_10 = closes.pct_change(10)

    def _last(series: pd.Series) -> float | None:
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None

    return {
        "rsi": _last(rsi_val),
        "macd_line": _last(macd_l),
        "macd_signal": _last(macd_s),
        "macd_hist": _last(macd_h),
        "bb_upper": _last(bb_u),
        "bb_middle": _last(bb_m),
        "bb_lower": _last(bb_lo),
        "vwap": _last(vwap_val),
        "atr": _last(atr_val),
        "volume_sma": _last(vol_sma_val),
        "close": float(closes.iloc[-1]),
        "volume": float(volumes.iloc[-1]),
        "williams_r": _last(wr_val),
        "ema_9": _last(ema_9),
        "ema_21": _last(ema_21),
        "momentum_5": _last(mom_5),
        "momentum_10": _last(mom_10),
    }

"""Technical indicators for crypto price analysis.

Includes the Adaptive Momentum strategy indicators: RSI(5), MACD(8-17-9),
EMA(8/21), VWAP, ATR(14), volume ratio, and daily MACD filter.
"""

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


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def volume_sma(volumes: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return volumes.rolling(window=period).mean()


def williams_r(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Williams %R oscillator (-100 to 0)."""
    highest = highs.rolling(window=period).max()
    lowest = lows.rolling(window=period).min()
    wr = -100 * (highest - closes) / (highest - lowest).replace(0, np.nan)
    return wr


def keltner_channel(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    ema_period: int = 20, atr_period: int = 14, multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Upper, middle (EMA), lower Keltner Channel."""
    middle = closes.ewm(span=ema_period, adjust=False).mean()
    atr_val = atr(highs, lows, closes, atr_period)
    upper = middle + multiplier * atr_val
    lower = middle - multiplier * atr_val
    return upper, middle, lower


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def macd_crossover(macd_line: pd.Series, signal_line: pd.Series) -> pd.Series:
    """True on bars where MACD crosses above signal (bullish crossover)."""
    prev_macd = macd_line.shift(1)
    prev_signal = signal_line.shift(1)
    return (prev_macd <= prev_signal) & (macd_line > signal_line)


def macd_crossunder(macd_line: pd.Series, signal_line: pd.Series) -> pd.Series:
    """True on bars where MACD crosses below signal (bearish crossover)."""
    prev_macd = macd_line.shift(1)
    prev_signal = signal_line.shift(1)
    return (prev_macd >= prev_signal) & (macd_line < signal_line)


def compute_all(bars: list[dict]) -> dict[str, float | None]:
    """Compute all indicators from a list of OHLCV bar dicts.

    Returns latest values for each indicator, or None if insufficient data.
    Includes Adaptive Momentum strategy indicators: RSI(5), MACD(8-17-9),
    EMA(8/21), volume ratio, and crossover states.
    """
    if len(bars) < 30:
        return {
            "rsi": None, "rsi_5": None,
            "macd_line": None, "macd_signal": None, "macd_hist": None,
            "macd_4h_line": None, "macd_4h_signal": None, "macd_4h_hist": None,
            "macd_4h_bullish_cross": False, "macd_4h_bearish_cross": False,
            "bb_upper": None, "bb_middle": None, "bb_lower": None,
            "vwap": None, "atr": None, "volume_sma": None,
            "williams_r": None, "ema_9": None, "ema_21": None,
            "momentum_5": None, "momentum_10": None,
            "vol_ratio_20": None,
        }

    df = pd.DataFrame(bars)
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    volumes = df["volume"].astype(float)

    rsi_14 = rsi(closes, 14)
    rsi_5 = rsi(closes, 5)

    macd_l, macd_s, macd_h = macd(closes)

    macd_4h_l, macd_4h_s, macd_4h_h = macd(closes, fast=8, slow=17, signal=9)
    macd_4h_bull = macd_crossover(macd_4h_l, macd_4h_s)
    macd_4h_bear = macd_crossunder(macd_4h_l, macd_4h_s)

    bb_u, bb_m, bb_lo = bollinger_bands(closes)
    vwap_val = vwap(highs, lows, closes, volumes)
    atr_val = atr(highs, lows, closes)
    vol_sma_val = volume_sma(volumes)
    wr_val = williams_r(highs, lows, closes)
    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    mom_5 = closes.pct_change(5)
    mom_10 = closes.pct_change(10)

    kc_u, kc_m, kc_l = keltner_channel(highs, lows, closes)
    ema_8 = ema(closes, 8)
    ema_13 = ema(closes, 13)
    ema_34 = ema(closes, 34)
    ema_55 = ema(closes, 55)
    mom_20 = closes.pct_change(20)
    vol_mom = volumes.pct_change(5)

    atr_sma_20 = atr_val.rolling(window=20).mean()

    vol_ratio = volumes / vol_sma_val

    sma_200 = sma(closes, min(200, len(closes)))

    def _last(series: pd.Series) -> float | None:
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None

    def _last_bool(series: pd.Series) -> bool:
        val = series.iloc[-1]
        return bool(val) if pd.notna(val) else False

    return {
        "rsi": _last(rsi_14),
        "rsi_5": _last(rsi_5),
        "macd_line": _last(macd_l),
        "macd_signal": _last(macd_s),
        "macd_hist": _last(macd_h),
        "macd_4h_line": _last(macd_4h_l),
        "macd_4h_signal": _last(macd_4h_s),
        "macd_4h_hist": _last(macd_4h_h),
        "macd_4h_bullish_cross": _last_bool(macd_4h_bull),
        "macd_4h_bearish_cross": _last_bool(macd_4h_bear),
        "bb_upper": _last(bb_u),
        "bb_middle": _last(bb_m),
        "bb_lower": _last(bb_lo),
        "vwap": _last(vwap_val),
        "atr": _last(atr_val),
        "atr_sma_20": _last(atr_sma_20),
        "volume_sma": _last(vol_sma_val),
        "vol_ratio_20": _last(vol_ratio),
        "close": float(closes.iloc[-1]),
        "high": float(highs.iloc[-1]),
        "low": float(lows.iloc[-1]),
        "volume": float(volumes.iloc[-1]),
        "williams_r": _last(wr_val),
        "ema_8": _last(ema_8),
        "ema_9": _last(ema_9),
        "ema_13": _last(ema_13),
        "ema_21": _last(ema_21),
        "ema_34": _last(ema_34),
        "ema_55": _last(ema_55),
        "sma_200": _last(sma_200),
        "momentum_5": _last(mom_5),
        "momentum_10": _last(mom_10),
        "momentum_20": _last(mom_20),
        "volume_momentum": _last(vol_mom),
        "kc_upper": _last(kc_u),
        "kc_middle": _last(kc_m),
        "kc_lower": _last(kc_l),
    }


def compute_confluence(
    tf_indicators: dict[str, dict[str, float | None]],
) -> dict[str, float]:
    """Score multi-timeframe signal alignment.

    tf_indicators: {"1m": indicators, "15m": indicators, "1h": indicators}
    Returns confluence multiplier and per-tf direction.
    """
    directions: dict[str, int] = {}
    for tf, ind in tf_indicators.items():
        if not ind or ind.get("ema_9") is None:
            continue
        score = 0
        if (ind.get("ema_9") or 0) > (ind.get("ema_21") or 0):
            score += 1
        else:
            score -= 1
        if (ind.get("macd_hist") or 0) > 0:
            score += 1
        else:
            score -= 1
        if (ind.get("rsi") or 50) < 30:
            score += 1
        elif (ind.get("rsi") or 50) > 70:
            score -= 1
        directions[tf] = 1 if score > 0 else (-1 if score < 0 else 0)

    if not directions:
        return {"multiplier": 0.5, "alignment": 0}

    vals = list(directions.values())
    if all(v > 0 for v in vals):
        alignment = 1.0
    elif all(v < 0 for v in vals):
        alignment = -1.0
    elif len(set(v for v in vals if v != 0)) <= 1:
        alignment = 0.6 * (1 if sum(vals) > 0 else -1)
    else:
        alignment = 0.0

    multiplier = abs(alignment) if alignment != 0 else 0.3

    return {
        "multiplier": round(multiplier, 2),
        "alignment": round(alignment, 2),
        "directions": directions,
    }

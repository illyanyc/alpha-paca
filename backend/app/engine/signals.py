"""Signal processing utilities: normalisation, IC computation, weighting."""

from __future__ import annotations

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

try:
    from scipy.stats import spearmanr as _spearmanr  # type: ignore[import-untyped]

    def _rank_correlation(a: np.ndarray, b: np.ndarray) -> float:
        corr, _ = _spearmanr(a, b)
        return float(corr)

except ImportError:
    logger.warning("scipy not available — falling back to numpy rank correlation")

    def _rank_correlation(a: np.ndarray, b: np.ndarray) -> float:
        """Spearman rank correlation via numpy (no scipy dependency)."""
        def _rank(arr: np.ndarray) -> np.ndarray:
            order = arr.argsort()
            ranks = np.empty_like(order, dtype=float)
            ranks[order] = np.arange(1, len(arr) + 1, dtype=float)
            return ranks

        return float(np.corrcoef(_rank(a), _rank(b))[0, 1])


class SignalProcessor:
    """Stateless signal maths used by strategy pods and the alpha model."""

    @staticmethod
    def normalize_signal(raw_values: np.ndarray) -> np.ndarray:
        """Z-score normalisation (mean=0, std=1)."""
        std = np.nanstd(raw_values)
        if std == 0:
            return np.zeros_like(raw_values)
        return (raw_values - np.nanmean(raw_values)) / std

    @staticmethod
    def compute_ic(
        signal_values: np.ndarray,
        forward_returns: np.ndarray,
    ) -> float:
        """Information coefficient — rank correlation between signal and subsequent returns."""
        mask = ~(np.isnan(signal_values) | np.isnan(forward_returns))
        s = signal_values[mask]
        r = forward_returns[mask]
        if len(s) < 5:
            return 0.0
        return _rank_correlation(s, r)

    @staticmethod
    def weight_by_ic(
        signals: np.ndarray,
        ic_values: np.ndarray,
    ) -> np.ndarray:
        """IC-weighted combination of signal columns.

        ``signals``  — (N, K) matrix with K signal columns for N symbols.
        ``ic_values`` — length-K vector of information coefficients.
        """
        clipped = np.clip(ic_values, 0, None)
        total = clipped.sum()
        if total == 0:
            return np.nanmean(signals, axis=1)
        weights = clipped / total
        return signals @ weights

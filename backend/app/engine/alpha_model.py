"""IC-weighted alpha combination across strategy pods."""

from __future__ import annotations

import numpy as np
import structlog

from app.engine.factor_model import FactorModel
from app.engine.signals import SignalProcessor

logger = structlog.get_logger(__name__)

DECAY_HALFLIFE_HOURS: float = 6.0
DEFAULT_VOL_TARGET: float = 0.15


class AlphaModel:
    """Combines alpha signals from multiple pods into final position weights."""

    def __init__(
        self,
        signal_processor: SignalProcessor,
        factor_model: FactorModel,
    ) -> None:
        self._sp = signal_processor
        self._fm = factor_model

    def combine_pod_signals(
        self,
        pod_signals: dict[str, np.ndarray],
    ) -> np.ndarray:
        """Cross-pod alpha combination with overlap penalty.

        Each pod contributes an array of alpha scores (one per symbol).
        Overlapping positions across pods are penalised to reduce concentration.
        """
        if not pod_signals:
            return np.array([])

        stacked = np.column_stack(list(pod_signals.values()))
        combined = np.nanmean(stacked, axis=1)

        overlap_count = np.sum(~np.isnan(stacked) & (stacked != 0), axis=1)
        penalty = np.where(overlap_count > 1, 1.0 / np.sqrt(overlap_count), 1.0)

        result = combined * penalty
        logger.info(
            "pod_signals_combined",
            pods=list(pod_signals.keys()),
            result_size=len(result),
        )
        return result

    def apply_decay_adjustment(
        self,
        alphas: np.ndarray,
        signal_ages_hours: np.ndarray,
    ) -> np.ndarray:
        """Exponential decay — stale signals are down-weighted."""
        decay = np.exp(-np.log(2) * signal_ages_hours / DECAY_HALFLIFE_HOURS)
        return alphas * decay

    def apply_transaction_cost_penalty(
        self,
        alphas: np.ndarray,
        cost_estimates: np.ndarray,
    ) -> np.ndarray:
        """Reduce alpha by estimated round-trip transaction costs."""
        return alphas - cost_estimates

    def compute_position_weights(
        self,
        alphas: np.ndarray,
        vol_targets: np.ndarray | None = None,
    ) -> np.ndarray:
        """Vol-targeting position sizing.

        Scales each alpha so the resulting position contributes equally
        to portfolio volatility (inverse-vol weighting).
        """
        if vol_targets is None:
            vol_targets = np.full_like(alphas, DEFAULT_VOL_TARGET)

        safe_vol = np.where(vol_targets > 0, vol_targets, DEFAULT_VOL_TARGET)
        raw_weights = alphas / safe_vol
        total = np.nansum(np.abs(raw_weights))
        if total == 0:
            return np.zeros_like(alphas)
        return raw_weights / total

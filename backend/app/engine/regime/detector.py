"""Hidden Markov Model regime detector for market state classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from app.engine.regime.models import RegimeOutput, RegimeState
from app.services.alpaca_client import AlpacaService

logger = structlog.get_logger(__name__)

N_REGIMES = 4
TRAINING_WINDOW_DAYS = 252
MIN_OBSERVATIONS = 60


class RegimeDetector:
    """Classifies market regime using a Gaussian HMM on return/volatility features."""

    def __init__(self, alpaca: AlpacaService) -> None:
        self._alpaca = alpaca
        self._model: Any | None = None
        self._last_trained: datetime | None = None
        self._retrain_interval = timedelta(days=7)
        self._state_order: list[int] = list(range(N_REGIMES))

    def detect(self, benchmark: str = "SPY") -> RegimeOutput:
        features = self._build_features(benchmark)
        if features is None or len(features) < MIN_OBSERVATIONS:
            logger.warning("regime_insufficient_data", benchmark=benchmark)
            return RegimeOutput(
                probabilities={s.value: 0.25 for s in RegimeState},
                dominant=RegimeState.SIDEWAYS,
                confidence=0.25,
            )

        if self._needs_retrain():
            self._train(features)

        if self._model is None:
            self._train(features)

        if self._model is None:
            return RegimeOutput(
                probabilities={s.value: 0.25 for s in RegimeState},
                dominant=RegimeState.SIDEWAYS,
                confidence=0.25,
            )

        try:
            latest = features[-1:].reshape(1, -1)
            raw_posteriors = self._model.predict_proba(latest)[0]
            # _state_order is ascending by mean return (lowest → highest)
            # Enum order: BULL(0), BEAR(1), SIDEWAYS(2), CRISIS(3)
            # Map: BULL ← highest return, BEAR ← 2nd-lowest, SIDEWAYS ← 2nd-highest, CRISIS ← lowest
            label_map = [
                self._state_order[3],  # BULL_TREND ← highest mean return
                self._state_order[1],  # BEAR_TREND ← 2nd-lowest
                self._state_order[2],  # SIDEWAYS ← 2nd-highest
                self._state_order[0],  # CRISIS ← lowest mean return
            ]
            posteriors = [float(raw_posteriors[label_map[i]]) for i in range(N_REGIMES)]
            return RegimeOutput.from_hmm_posteriors(posteriors)
        except Exception:
            logger.exception("regime_prediction_failed")
            return RegimeOutput(
                probabilities={s.value: 0.25 for s in RegimeState},
                dominant=RegimeState.SIDEWAYS,
                confidence=0.25,
            )

    def _build_features(self, benchmark: str) -> np.ndarray | None:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=TRAINING_WINDOW_DAYS + 30)
            barset = self._alpaca.get_bars(benchmark, "1Day", start, end)
            bars = barset.data.get(benchmark, []) if hasattr(barset, "data") else barset.get(benchmark, [])
            if len(bars) < MIN_OBSERVATIONS:
                return None

            closes = np.array([float(b.close) for b in bars])
            volumes = np.array([float(b.volume) for b in bars])
            highs = np.array([float(b.high) for b in bars])
            lows = np.array([float(b.low) for b in bars])

            log_returns = np.diff(np.log(closes))

            vol_window = 20
            realized_vol = np.array([
                np.std(log_returns[max(0, i - vol_window):i]) * np.sqrt(252)
                for i in range(1, len(log_returns) + 1)
            ])

            vol_sma = np.convolve(volumes[1:], np.ones(vol_window) / vol_window, mode="same")
            volume_ratio = np.where(vol_sma > 0, volumes[1:] / vol_sma, 1.0)

            intraday_range = np.where(
                lows[1:] > 0, (highs[1:] - lows[1:]) / lows[1:], 0.0
            )

            n = min(len(log_returns), len(realized_vol), len(volume_ratio), len(intraday_range))
            features = np.column_stack([
                log_returns[-n:],
                realized_vol[-n:],
                volume_ratio[-n:],
                intraday_range[-n:],
            ])

            mask = np.all(np.isfinite(features), axis=1)
            features = features[mask]

            return features if len(features) >= MIN_OBSERVATIONS else None
        except Exception:
            logger.exception("regime_feature_build_failed", benchmark=benchmark)
            return None

    def _train(self, features: np.ndarray) -> None:
        try:
            from hmmlearn.hmm import GaussianHMM

            model = GaussianHMM(
                n_components=N_REGIMES,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            model.fit(features)

            mean_returns = model.means_[:, 0]
            sorted_indices = np.argsort(mean_returns)
            self._state_order = sorted_indices.tolist()

            self._model = model
            self._last_trained = datetime.now(timezone.utc)
            logger.info(
                "regime_model_trained",
                n_obs=len(features),
                n_states=N_REGIMES,
                mean_returns=[float(mean_returns[i]) for i in sorted_indices],
            )
        except ImportError:
            logger.warning("hmmlearn_not_installed_regime_detection_disabled")
            self._model = None
        except Exception:
            logger.exception("regime_training_failed")
            self._model = None

    def _needs_retrain(self) -> bool:
        if self._model is None or self._last_trained is None:
            return True
        return datetime.now(timezone.utc) - self._last_trained > self._retrain_interval

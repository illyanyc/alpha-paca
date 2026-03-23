"""Autoregressive Drift Detection Method (ADDM) for concept drift monitoring."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

WINDOW_SIZE = 100
DRIFT_THRESHOLD = 2.5
WARNING_THRESHOLD = 2.0


class DriftSeverity(str, Enum):
    NONE = "none"
    WARNING = "warning"
    DRIFT = "drift"
    SEVERE = "severe"


class DriftEvent:
    __slots__ = ("severity", "score", "timestamp", "pod_name", "details")

    def __init__(
        self,
        severity: DriftSeverity,
        score: float,
        pod_name: str,
        details: str = "",
    ) -> None:
        self.severity = severity
        self.score = score
        self.timestamp = datetime.now(timezone.utc)
        self.pod_name = pod_name
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "score": round(self.score, 4),
            "timestamp": self.timestamp.isoformat(),
            "pod_name": self.pod_name,
            "details": self.details,
        }


class DriftDetector:
    """Monitors prediction residuals for concept drift using ADDM.

    Tracks the error series from signal predictions. When the error
    distribution shifts beyond a threshold (detected via a rolling z-score
    on the residual mean), it signals drift.
    """

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self._window_size = window_size
        self._residuals: dict[str, deque[float]] = {}
        self._baselines: dict[str, tuple[float, float]] = {}
        self._events: list[DriftEvent] = []

    def record_residual(self, pod_name: str, predicted: float, actual: float) -> None:
        """Record a prediction residual for a given pod."""
        residual = actual - predicted
        if pod_name not in self._residuals:
            self._residuals[pod_name] = deque(maxlen=self._window_size * 2)
        self._residuals[pod_name].append(residual)

    def check_drift(self, pod_name: str) -> DriftEvent:
        """Check for drift in a specific pod's residual series."""
        residuals = self._residuals.get(pod_name, deque())
        if len(residuals) < self._window_size:
            return DriftEvent(DriftSeverity.NONE, 0.0, pod_name, "insufficient_data")

        arr = np.array(residuals)
        baseline_window = arr[: self._window_size // 2]
        recent_window = arr[-(self._window_size // 2) :]

        baseline_mean = float(np.mean(baseline_window))
        baseline_std = float(np.std(baseline_window))
        recent_mean = float(np.mean(recent_window))

        if baseline_std < 1e-9:
            baseline_std = 1e-9

        drift_score = abs(recent_mean - baseline_mean) / baseline_std

        if drift_score >= DRIFT_THRESHOLD * 1.5:
            severity = DriftSeverity.SEVERE
        elif drift_score >= DRIFT_THRESHOLD:
            severity = DriftSeverity.DRIFT
        elif drift_score >= WARNING_THRESHOLD:
            severity = DriftSeverity.WARNING
        else:
            severity = DriftSeverity.NONE

        event = DriftEvent(
            severity=severity,
            score=drift_score,
            pod_name=pod_name,
            details=f"baseline_mean={baseline_mean:.4f} recent_mean={recent_mean:.4f}",
        )

        if severity in (DriftSeverity.DRIFT, DriftSeverity.SEVERE):
            self._events.append(event)
            logger.warning(
                "concept_drift_detected",
                pod=pod_name,
                severity=severity.value,
                score=drift_score,
            )

        return event

    def check_all(self) -> list[DriftEvent]:
        """Check drift across all tracked pods."""
        events = []
        for pod_name in list(self._residuals.keys()):
            event = self.check_drift(pod_name)
            if event.severity != DriftSeverity.NONE:
                events.append(event)
        return events

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._events[-limit:]]

    def get_pod_stats(self, pod_name: str) -> dict[str, Any]:
        residuals = self._residuals.get(pod_name, deque())
        if not residuals:
            return {"pod_name": pod_name, "n_samples": 0}
        arr = np.array(residuals)
        return {
            "pod_name": pod_name,
            "n_samples": len(arr),
            "mean_residual": float(np.mean(arr)),
            "std_residual": float(np.std(arr)),
            "max_abs_residual": float(np.max(np.abs(arr))),
        }

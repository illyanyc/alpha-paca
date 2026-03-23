"""Regime state definitions and pod weight allocation matrix."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RegimeState(str, Enum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    SIDEWAYS = "sideways"
    CRISIS = "crisis"


class RegimeOutput(BaseModel):
    """Output of regime detection: probabilities for each state + dominant state."""

    probabilities: dict[str, float]
    dominant: RegimeState
    confidence: float

    @classmethod
    def from_hmm_posteriors(cls, posteriors: list[float]) -> RegimeOutput:
        states = list(RegimeState)
        prob_dict = {s.value: round(p, 4) for s, p in zip(states, posteriors)}
        max_idx = posteriors.index(max(posteriors))
        return cls(
            probabilities=prob_dict,
            dominant=states[max_idx],
            confidence=posteriors[max_idx],
        )


REGIME_POD_WEIGHTS: dict[RegimeState, dict[str, float]] = {
    RegimeState.BULL_TREND: {
        "momentum": 1.8,
        "mean_reversion": 0.6,
        "event_driven": 1.0,
        "stat_arb": 1.0,
        "sector_rotation": 1.2,
        "volatility": 0.6,
    },
    RegimeState.BEAR_TREND: {
        "momentum": 1.6,
        "mean_reversion": 0.6,
        "event_driven": 0.8,
        "stat_arb": 1.2,
        "sector_rotation": 0.8,
        "volatility": 0.6,
    },
    RegimeState.SIDEWAYS: {
        "momentum": 0.4,
        "mean_reversion": 1.8,
        "event_driven": 1.0,
        "stat_arb": 1.4,
        "sector_rotation": 0.8,
        "volatility": 0.4,
    },
    RegimeState.CRISIS: {
        "momentum": 0.2,
        "mean_reversion": 0.4,
        "event_driven": 0.6,
        "stat_arb": 1.0,
        "sector_rotation": 0.4,
        "volatility": 2.4,
    },
}

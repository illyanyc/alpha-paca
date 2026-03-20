"""Validation result and context models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ValidationResult(BaseModel):
    validator_name: str
    verdict: Literal["pass", "fail", "warn"]
    reason: str


class ValidatorContext(dict[str, Any]):
    """Dict subclass used to carry context through the validator pipeline.

    Supports attribute-style access for convenience while remaining
    serialisable as a plain ``dict``.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

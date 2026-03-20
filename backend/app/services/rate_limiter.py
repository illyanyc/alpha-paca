"""Async token-bucket style rate limiter."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Limits async callers to at most `max_calls` per `period` seconds."""

    def __init__(self, max_calls: int, period: float) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if period <= 0:
            raise ValueError("period must be > 0")
        self.max_calls = max_calls
        self.period = period
        self._lock = asyncio.Lock()
        self._window_start = time.monotonic()
        self._calls_in_window = 0

    async def acquire(self) -> None:
        """Wait until a call slot is available, then record the call."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._window_start
            if elapsed >= self.period:
                self._window_start = now
                self._calls_in_window = 0
            if self._calls_in_window >= self.max_calls:
                sleep_for = self.period - elapsed
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                self._window_start = time.monotonic()
                self._calls_in_window = 0
            self._calls_in_window += 1

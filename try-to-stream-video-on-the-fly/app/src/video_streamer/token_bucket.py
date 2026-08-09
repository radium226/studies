"""Generic token bucket: refills over wall-clock time, capped at a capacity.

Gate-then-spend, not reserve-then-adjust: `try_acquire` only reports whether
enough budget is currently available; the caller reports how much was
actually used afterward via `record_spend`. This fits work whose cost isn't
known until after it runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._clock = clock
        self._budget = capacity
        self._last_refill = clock()

    def try_acquire(self, threshold: float) -> bool:
        now = self._clock()
        self._budget = min(
            self._capacity, self._budget + (now - self._last_refill) * self._refill_rate
        )
        self._last_refill = now
        return self._budget >= threshold

    def record_spend(self, amount: float) -> None:
        self._budget = max(0.0, self._budget - amount)

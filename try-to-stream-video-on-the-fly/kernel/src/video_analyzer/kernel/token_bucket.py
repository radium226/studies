from loguru import logger

from .services import Clock


class TokenBucket:

    def __init__(self, capacity: float, refill_rate: float, clock: Clock) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.clock = clock
        self.budget = capacity
        self.last_refill_time = clock.now()
        logger.debug(
            "TokenBucket configured: capacity={}, refill_rate={}",
            capacity,
            refill_rate,
        )

    def try_acquire(self, required_budget: float) -> bool:
        now = self.clock.now()
        self.budget = min(
            self.capacity,
            self.budget + (now - self.last_refill_time) * self.refill_rate,
        )
        self.last_refill_time = now
        acquired = self.budget >= required_budget
        logger.trace(
            "TokenBucket: try_acquire({}) -> {} (budget {:.3f} of {:.3f})",
            required_budget,
            acquired,
            self.budget,
            self.capacity,
        )
        return acquired

    def record_spend(self, spent_budget: float) -> None:
        self.budget = max(0.0, self.budget - spent_budget)
        logger.trace(
            "TokenBucket: spent {:.3f}, budget now {:.3f}",
            spent_budget,
            self.budget,
        )

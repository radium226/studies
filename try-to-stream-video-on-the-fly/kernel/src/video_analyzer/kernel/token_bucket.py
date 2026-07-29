from loguru import logger

from .services import Clock


class TokenBucket:

    def __init__(self, capacity: float, refill_rate: float, clock: Clock) -> None:
        self.token_capacity = capacity
        self.refill_rate = refill_rate
        self.clock = clock
        self.available_tokens = capacity
        self.last_refill_time = clock.now()
        logger.debug(
            "TokenBucket configured: capacity={}, refill_rate={}",
            capacity,
            refill_rate,
        )

    def try_acquire(self, tokens_needed: float) -> bool:
        now = self.clock.now()
        self.available_tokens = min(
            self.token_capacity,
            self.available_tokens + (now - self.last_refill_time) * self.refill_rate,
        )
        self.last_refill_time = now
        acquired = self.available_tokens >= tokens_needed
        logger.trace(
            "TokenBucket: try_acquire({}) -> {} (tokens {:.3f} of {:.3f})",
            tokens_needed,
            acquired,
            self.available_tokens,
            self.token_capacity,
        )
        return acquired

    def record_spend(self, tokens_spent: float) -> None:
        self.available_tokens = max(0.0, self.available_tokens - tokens_spent)
        logger.trace(
            "TokenBucket: spent {:.3f}, tokens now {:.3f}",
            tokens_spent,
            self.available_tokens,
        )

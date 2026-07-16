from loguru import logger

from .services import Clock
from .token_bucket import TokenBucket


class BatchGate:

    def __init__(
        self,
        clock: Clock,
        frames_per_second: float,
        max_batch_frames: int,
        max_batch_lag_ms: float,
    ) -> None:
        self.clock = clock
        self.frames_per_second = frames_per_second
        self.max_batch_frames = max(1, max_batch_frames)
        self.max_batch_lag_ms = max(0.0, max_batch_lag_ms)
        self.token_bucket = TokenBucket(
            capacity=1.0, refill_rate=frames_per_second, clock=clock
        )
        self.eligible_since: float | None = None
        logger.debug(
            "BatchGate configured: frames_per_second={}, max_batch_frames={}, "
            "max_batch_lag_ms={}",
            frames_per_second,
            self.max_batch_frames,
            self.max_batch_lag_ms,
        )

    def reset(self) -> None:
        self.eligible_since = None

    def should_fire(self, available_frames: int) -> bool:
        if not self.token_bucket.try_acquire(1.0):
            logger.trace(
                "BatchGate: token bucket empty ({} frames available)",
                available_frames,
            )
            self.reset()
            return False
        if available_frames <= 0:
            self.reset()
            return False
        now = self.clock.now()
        if self.eligible_since is None:
            logger.trace(
                "BatchGate: became eligible at {} ({} frames available)",
                now,
                available_frames,
            )
            self.eligible_since = now
        batch_full = available_frames >= self.max_batch_frames
        lag_ms = (now - self.eligible_since) * 1000.0
        lag_exceeded = lag_ms >= self.max_batch_lag_ms
        if not batch_full and not lag_exceeded:
            logger.trace(
                "BatchGate: holding — {} of {} frames, lag {:.1f} of {:.1f} ms",
                available_frames,
                self.max_batch_frames,
                lag_ms,
                self.max_batch_lag_ms,
            )
            return False
        logger.debug(
            "BatchGate: firing with {} frames ({}, lag {:.1f} ms)",
            available_frames,
            "batch full" if batch_full else "lag exceeded",
            lag_ms,
        )
        self.reset()
        return True

    def record_spend(self, elapsed_seconds: float) -> None:
        spent_budget = elapsed_seconds * self.frames_per_second
        logger.trace(
            "BatchGate: recording spend of {:.3f} s ({:.2f} tokens)",
            elapsed_seconds,
            spent_budget,
        )
        self.token_bucket.record_spend(spent_budget)

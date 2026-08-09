from loguru import logger

from .config import BatchGateConfig, TokenBucketConfig
from .services import Clock
from .token_bucket import TokenBucket


class BatchGate:
    """Decides when a detection batch fires: once the detection budget allows
    it, fire when the batch is full (`max_frames` frames waiting) or has
    waited long enough (`max_lag_ms`), whichever comes first.

    Built on `TokenBucket`'s gate-then-spend protocol: `should_fire` only
    *checks* the budget; after running the batch the caller reports its actual
    duration via `record_spend`, which is what adapts detection frequency to
    what inference really costs on this machine.

    Subtlety: the epoch `max_lag_ms` is measured from (`eligible_since`)
    resets whenever the token bucket is empty or no frames are waiting — the
    lag clock starts when the gate becomes *able* to fire, not when frames
    first started queuing."""

    def __init__(
        self,
        clock: Clock,
        frames_per_second: float,
        *,
        config: BatchGateConfig | None = None,
    ) -> None:
        self.clock = clock
        # Not configuration: this is the source's own frame rate, probed at
        # runtime. It is the detection *budget*, in tokens per wall-clock
        # second.
        self.frames_per_second = frames_per_second
        self.config = config if config is not None else BatchGateConfig()
        self.token_bucket = TokenBucket(
            clock,
            refill_rate=frames_per_second,
            # A capacity of exactly one token is the gate's own invariant, not
            # a knob: it is what limits the gate to one pass worth of banked
            # budget, so an idle stretch can't buy a burst of back-to-back
            # detection passes.
            config=TokenBucketConfig(capacity=1.0),
        )
        self.eligible_since: float | None = None
        logger.debug(
            "BatchGate configured: frames_per_second={}, {}",
            frames_per_second,
            self.config,
        )

    def reset(self) -> None:
        self.eligible_since = None

    def should_fire(self, available_frames: int) -> bool:
        if not self.token_bucket.can_spend(1.0):
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
        batch_full = available_frames >= self.config.max_frames
        lag_ms = (now - self.eligible_since) * 1000.0
        lag_exceeded = lag_ms >= self.config.max_lag_ms
        if not batch_full and not lag_exceeded:
            logger.trace(
                "BatchGate: holding — {} of {} frames, lag {:.1f} of {:.1f} ms",
                available_frames,
                self.config.max_frames,
                lag_ms,
                self.config.max_lag_ms,
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

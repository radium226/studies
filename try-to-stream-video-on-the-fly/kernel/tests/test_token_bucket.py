from video_analyzer.kernel import Clock, TokenBucket


class ManualClock(Clock):
    def __init__(self) -> None:
        self.time = 0.0

    def now(self) -> float:
        return self.time


def test_initial_budget_is_full() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=2.0, refill_rate=1.0, clock=clock)
    assert bucket.try_acquire(2.0)


def test_spend_depletes_budget() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=1.0, refill_rate=1.0, clock=clock)
    assert bucket.try_acquire(1.0)
    bucket.record_spend(1.0)
    assert not bucket.try_acquire(1.0)


def test_refill_over_time() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=1.0, refill_rate=1.0, clock=clock)
    bucket.record_spend(1.0)
    assert not bucket.try_acquire(1.0)
    clock.time += 1.0
    assert bucket.try_acquire(1.0)


def test_refill_caps_at_capacity() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=1.0, refill_rate=10.0, clock=clock)
    clock.time += 100.0
    bucket.try_acquire(0.0)  # trigger a refill computation
    bucket.record_spend(1.0)
    # if refill had gone unbounded above capacity, one more spend of 1.0
    # wouldn't have exhausted it
    assert not bucket.try_acquire(1.0)

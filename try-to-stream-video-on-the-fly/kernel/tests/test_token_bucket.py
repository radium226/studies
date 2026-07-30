from video_analyzer.kernel import Clock, TokenBucket, TokenBucketConfig


class ManualClock(Clock):
    def __init__(self) -> None:
        self.time = 0.0

    def now(self) -> float:
        return self.time


def test_initial_budget_is_full() -> None:
    clock = ManualClock()
    bucket = TokenBucket(clock, 1.0, config=TokenBucketConfig(capacity=2.0))
    assert bucket.can_spend(2.0)


def test_spend_depletes_budget() -> None:
    clock = ManualClock()
    bucket = TokenBucket(clock, 1.0, config=TokenBucketConfig(capacity=1.0))
    assert bucket.can_spend(1.0)
    bucket.record_spend(1.0)
    assert not bucket.can_spend(1.0)


def test_refill_over_time() -> None:
    clock = ManualClock()
    bucket = TokenBucket(clock, 1.0, config=TokenBucketConfig(capacity=1.0))
    bucket.record_spend(1.0)
    assert not bucket.can_spend(1.0)
    clock.time += 1.0
    assert bucket.can_spend(1.0)


def test_refill_caps_at_capacity() -> None:
    clock = ManualClock()
    bucket = TokenBucket(clock, 10.0, config=TokenBucketConfig(capacity=1.0))
    clock.time += 100.0
    bucket.can_spend(0.0)  # trigger a refill computation
    bucket.record_spend(1.0)
    # if refill had gone unbounded above capacity, one more spend of 1.0
    # wouldn't have exhausted it
    assert not bucket.can_spend(1.0)

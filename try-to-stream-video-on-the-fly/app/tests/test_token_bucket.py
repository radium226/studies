"""TokenBucket: gate-then-spend throttle with a controllable clock."""

from video_streamer.token_bucket import TokenBucket


def make_bucket(capacity: float = 1.0, refill_rate: float = 2.0):
    now = [0.0]
    bucket = TokenBucket(capacity, refill_rate, clock=lambda: now[0])
    return bucket, now


def test_starts_full_and_acquire_does_not_consume() -> None:
    bucket, _ = make_bucket()
    assert bucket.try_acquire(1.0)
    assert bucket.try_acquire(1.0)  # gate only; spend is reported separately


def test_spend_drains_and_time_refills() -> None:
    bucket, now = make_bucket(capacity=1.0, refill_rate=2.0)
    bucket.record_spend(1.0)
    assert not bucket.try_acquire(1.0)
    now[0] += 0.25  # +0.5 tokens
    assert not bucket.try_acquire(1.0)
    now[0] += 0.25  # budget back to 1.0
    assert bucket.try_acquire(1.0)


def test_overspend_clamps_at_zero_not_negative() -> None:
    bucket, now = make_bucket(capacity=1.0, refill_rate=2.0)
    bucket.record_spend(100.0)  # detection took far longer than budgeted
    now[0] += 0.5  # would be +1.0 token from empty, not from -99
    assert bucket.try_acquire(1.0)


def test_refill_is_capped_at_capacity() -> None:
    bucket, now = make_bucket(capacity=1.0, refill_rate=2.0)
    now[0] += 100.0
    assert bucket.try_acquire(1.0)
    bucket.record_spend(1.0)
    assert not bucket.try_acquire(1.0)  # no banked surplus beyond capacity

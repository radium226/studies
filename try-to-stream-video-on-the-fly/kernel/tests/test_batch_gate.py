from video_analyzer.kernel import BatchGate, Clock


class ManualClock(Clock):
    def __init__(self) -> None:
        self.time = 0.0

    def now(self) -> float:
        return self.time


def test_fires_when_batch_full() -> None:
    clock = ManualClock()
    gate = BatchGate(clock, frames_per_second=30.0, max_frames=4, max_lag_ms=1000.0)
    assert not gate.should_fire(1)
    assert not gate.should_fire(2)
    assert not gate.should_fire(3)
    assert gate.should_fire(4)


def test_fires_when_lag_exceeded() -> None:
    clock = ManualClock()
    gate = BatchGate(clock, frames_per_second=30.0, max_frames=100, max_lag_ms=50.0)
    assert not gate.should_fire(1)
    clock.time += 0.1
    assert gate.should_fire(1)


def test_resets_after_firing() -> None:
    clock = ManualClock()
    gate = BatchGate(clock, frames_per_second=30.0, max_frames=1, max_lag_ms=0.0)
    assert gate.should_fire(1)
    assert gate.should_fire(1)


def test_no_fire_without_available_frames() -> None:
    clock = ManualClock()
    gate = BatchGate(clock, frames_per_second=30.0, max_frames=4, max_lag_ms=0.0)
    assert not gate.should_fire(0)


def test_token_bucket_gates_firing() -> None:
    clock = ManualClock()
    gate = BatchGate(clock, frames_per_second=1.0, max_frames=1, max_lag_ms=0.0)
    assert gate.should_fire(1)
    gate.record_spend(10.0)
    assert not gate.should_fire(1)

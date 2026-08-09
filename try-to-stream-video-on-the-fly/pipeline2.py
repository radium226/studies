# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "loguru",
# ]
# ///
"""Queue-based re-architecture of pipeline.py's call-driven core.

Where pipeline.py's Engine.process(frame) performs four phases inline per
call (buffer frame -> harvest finished batch -> maybe schedule batch -> emit
delayed frame), here each concern is an independent asyncio task owning its
own loop and state, connected by closable Channels:

    produce_frames ──frame_channel──► detect_frames ──annotated_channel──► render_frames ──output_channel──► broadcast_frames
                                        │ forwards every Frame immediately     │ delay buffer (pending frames)
                                        │ accumulates detection candidates     │ snapshot list + render cursor:
                                        │ BatchGate throttles; ONE in-flight   │ a frame is emitted only once it is
                                        │ batch task pushes Snapshots into     │ bracketed by two snapshots with
                                        │ the same ordered stream              │ `lookahead` snapshots beyond
"""

import asyncio
import time
from typing import AsyncIterator, Callable, Generator, Self
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager, asynccontextmanager, AsyncExitStack
from dataclasses import dataclass, field
from loguru import logger
from asyncio import Queue


# ──────────────────────────────────────────────────────────────────────────
# Channel — the seam between stages
# ──────────────────────────────────────────────────────────────────────────


class Channel[T]:
    """Bounded queue with an end-of-stream sentinel.

    Producers `send` items and `close` when done; consumers just
    `async for item in channel`, which terminates cleanly at the sentinel.
    """

    def __init__(self, maxsize: int = 0):
        self.queue: Queue[T | None] = Queue(maxsize)

    async def send(self, item: T) -> None:
        await self.queue.put(item)

    async def close(self) -> None:
        await self.queue.put(None)

    async def __aiter__(self) -> AsyncIterator[T]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


# ──────────────────────────────────────────────────────────────────────────
# Messages flowing between stages
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Frame[FrameT]:
    index: int
    payload: FrameT


@dataclass(frozen=True)
class Snapshot[DetectionT]:
    """Detections computed for one sampled frame."""

    frame_index: int
    detections: list[DetectionT]


@dataclass(frozen=True)
class AnnotatedFrame[FrameT, DetectionT]:
    """A frame emitted with the detection segment it lies inside.

    bracket is the (earlier, later) snapshot pair with
    earlier.frame_index <= frame.index <= later.frame_index; is_exact means
    the frame landed on a sampled index. flushed marks tail frames emitted
    at end-of-stream, where the lookahead requirement is relaxed.
    """

    frame: Frame[FrameT]
    bracket: tuple[Snapshot[DetectionT], Snapshot[DetectionT]] | None
    is_exact: bool
    flushed: bool = False


# ──────────────────────────────────────────────────────────────────────────
# TokenBucket + BatchGate — the detection throttle (ported from pipeline.py)
# ──────────────────────────────────────────────────────────────────────────


class TokenBucket:
    """Gate-then-spend throttle: `try_acquire` only checks the budget; the
    caller reports actual cost afterwards via `record_spend` (inference
    duration isn't known until after it runs)."""

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.clock = clock
        self.budget = capacity
        self.last_refill = clock()

    def try_acquire(self, threshold: float) -> bool:
        now = self.clock()
        self.budget = min(
            self.capacity,
            self.budget + (now - self.last_refill) * self.refill_rate,
        )
        self.last_refill = now
        return self.budget >= threshold

    def record_spend(self, amount: float) -> None:
        self.budget = max(0.0, self.budget - amount)


class BatchGate:
    """Decides when a detection batch may fire: a TokenBucket (in
    frame-budget units: detection seconds x fps) plus a wait-or-cap policy —
    hold an eligible batch until it is full (`batch_frames`) or
    `max_batch_lag_ms` has elapsed, whichever comes first."""

    def __init__(
        self,
        *,
        fps: float,
        batch_frames: int,
        max_batch_lag_ms: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fps = fps
        self.batch_frames = max(1, batch_frames)
        self.max_batch_lag_ms = max(0.0, max_batch_lag_ms)
        self.clock = clock
        self.budget = TokenBucket(capacity=1.0, refill_rate=fps, clock=clock)
        self.eligible_since: float | None = None

    def reset(self) -> None:
        self.eligible_since = None

    def should_fire(self, available_frames: int) -> bool:
        if not self.budget.try_acquire(1.0):
            self.reset()
            return False
        if available_frames <= 0:
            self.reset()
            return False
        now = self.clock()
        if self.eligible_since is None:
            self.eligible_since = now
        batch_full = available_frames >= self.batch_frames
        lag_exceeded = (now - self.eligible_since) * 1000.0 >= self.max_batch_lag_ms
        if not batch_full and not lag_exceeded:
            return False  # keep accumulating frames
        self.reset()
        return True

    def record_spend(self, elapsed_seconds: float) -> None:
        self.budget.record_spend(elapsed_seconds * self.fps)


# ──────────────────────────────────────────────────────────────────────────
# Components (unchanged .start() async-context-manager pattern)
# ──────────────────────────────────────────────────────────────────────────


class Source[FrameT](ABC):

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def pull_frame(self) -> FrameT | None: ...


class Broadcaster[FrameT]:

    def __init__(self):
        self.received: list[FrameT] = []

    @classmethod
    def start(cls) -> AbstractAsyncContextManager[Self]:
        @asynccontextmanager
        async def _start() -> AsyncIterator[Self]:
            yield cls()

        return _start()

    async def push_frame(self, frame: FrameT) -> None:
        self.received.append(frame)
        logger.info(f"Broadcast frame: {frame}")


class NumberSource(Source[int]):
    """Paced source: yields one number per frame interval, so a slow
    detector visibly overlaps several frame arrivals."""

    def __init__(self, numbers: Generator[int, None, None], frame_interval: float):
        super().__init__("numbers")
        self.numbers = numbers
        self.frame_interval = frame_interval

    @classmethod
    def start(cls, count: int, fps: float) -> AbstractAsyncContextManager[Self]:
        @asynccontextmanager
        async def _start() -> AsyncIterator[Self]:
            numbers = (i for i in range(count))
            try:
                yield cls(numbers, frame_interval=1.0 / fps)
            finally:
                numbers.close()

        return _start()

    async def pull_frame(self) -> int | None:
        value = next(self.numbers, None)
        if value is None:
            return None
        await asyncio.sleep(self.frame_interval)
        return value


class Detector[FrameT, DetectionT](ABC):

    @abstractmethod
    async def detect(self, frames: list[FrameT]) -> list[list[DetectionT]]:
        ...


class DivisorDetector(Detector[int, int]):

    def __init__(self, divisor: int):
        self.divisor = divisor
        self.max_batch_seen = 0
        self.sampled_payloads: list[int] = []

    @classmethod
    def start(cls, divisor: int) -> AbstractAsyncContextManager[Self]:
        @asynccontextmanager
        async def _start() -> AsyncIterator[Self]:
            yield cls(divisor)

        return _start()

    async def detect(self, frames: list[int]) -> list[list[int]]:
        self.max_batch_seen = max(self.max_batch_seen, len(frames))
        self.sampled_payloads.extend(frames)
        logger.info(f"Detecting batch of {len(frames)}: {frames}")
        await asyncio.sleep(0.25)  # slow, like real inference
        return [[frame] if frame % self.divisor == 0 else [] for frame in frames]


# ──────────────────────────────────────────────────────────────────────────
# Stages — each concern is one task, one loop, its own state
# ──────────────────────────────────────────────────────────────────────────


async def produce_frames[FrameT](
    source: Source[FrameT],
    out: Channel[Frame[FrameT]],
) -> None:
    """Source -> indexed Frame messages, then close."""
    index = 0
    while True:
        payload = await source.pull_frame()
        if payload is None:
            break
        await out.send(Frame(index, payload))
        index += 1
    await out.close()


def sample_evenly[T](candidates: list[T], count: int) -> list[T]:
    """Up to `count` evenly spread candidates, endpoints always included."""
    if len(candidates) <= count:
        return list(candidates)
    positions = sorted(
        {round(i * (len(candidates) - 1) / (count - 1)) for i in range(count)}
    )
    return [candidates[position] for position in positions]


async def detect_frames[FrameT, DetectionT](
    in_: Channel[Frame[FrameT]],
    out: Channel[Frame[FrameT] | Snapshot[DetectionT]],
    detector: Detector[FrameT, DetectionT],
    gate: BatchGate,
) -> None:
    """Forward every frame immediately; run throttled, batched detection
    concurrently, pushing Snapshots into the same ordered stream.

    A Snapshot for frame k can only ever land after Frame k itself: the
    frame is forwarded before the batch containing it even starts.
    """
    candidates: list[Frame[FrameT]] = []
    inflight: asyncio.Task[None] | None = None

    async def run_batch(batch: list[Frame[FrameT]]) -> None:
        started = time.monotonic()
        detections_per_frame = await detector.detect(
            [frame.payload for frame in batch]
        )
        gate.record_spend(time.monotonic() - started)
        for frame, detections in zip(batch, detections_per_frame, strict=True):
            await out.send(Snapshot(frame.index, detections))

    async for frame in in_:
        await out.send(frame)  # frames never wait on detection
        candidates.append(frame)

        if inflight is not None:
            if not inflight.done():
                gate.reset()  # a batch is in flight: keep accumulating
                continue
            inflight.result()  # surface a crashed batch instead of hiding it
            inflight = None

        if not gate.should_fire(len(candidates)):
            continue
        batch = sample_evenly(candidates, gate.batch_frames)
        candidates.clear()
        inflight = asyncio.create_task(run_batch(batch))

    # End of stream: drain the in-flight batch, flush leftover candidates as
    # one final batch, then close.
    if inflight is not None:
        await inflight
    if candidates:
        await run_batch(sample_evenly(candidates, gate.batch_frames))
    await out.close()


async def render_frames[FrameT, DetectionT](
    in_: Channel[Frame[FrameT] | Snapshot[DetectionT]],
    out: Channel[AnnotatedFrame[FrameT, DetectionT]],
    lookahead: int,
) -> None:
    """The delay buffer + render cursor: hold frames back until they are
    bracketed by two snapshots with `lookahead` snapshots beyond the
    bracket — pure interpolation, never extrapolation."""
    pending: dict[int, Frame[FrameT]] = {}  # insertion order == index order
    snapshots: list[Snapshot[DetectionT]] = []

    def find_segment(index: int) -> int | None:
        for i in range(len(snapshots) - 1):
            if snapshots[i].frame_index <= index <= snapshots[i + 1].frame_index:
                return i
        return None

    def annotate(
        frame: Frame[FrameT], segment: int | None, flushed: bool
    ) -> AnnotatedFrame[FrameT, DetectionT]:
        if segment is None:
            return AnnotatedFrame(frame, None, is_exact=False, flushed=flushed)
        earlier, later = snapshots[segment], snapshots[segment + 1]
        is_exact = frame.index in (earlier.frame_index, later.frame_index)
        return AnnotatedFrame(frame, (earlier, later), is_exact, flushed)

    async def emit_ready() -> None:
        while pending:
            oldest_index = next(iter(pending))
            segment = find_segment(oldest_index)
            if segment is None or len(snapshots) - (segment + 2) < lookahead:
                return  # not bracketed yet, or not enough lookahead beyond
            frame = pending.pop(oldest_index)
            await out.send(annotate(frame, segment, flushed=False))
            # Snapshots whose segment lies fully behind the next needed
            # frame can never bracket anything again.
            while len(snapshots) >= 2 and snapshots[1].frame_index < oldest_index + 1:
                snapshots.pop(0)

    async for message in in_:
        match message:
            case Frame() as frame:
                pending[frame.index] = frame
            case Snapshot() as snapshot:
                snapshots.append(snapshot)
        await emit_ready()

    # End of stream: no more snapshots are coming, so relax the lookahead
    # requirement and flush the tail with whatever bracket exists.
    for index, frame in pending.items():
        await out.send(annotate(frame, find_segment(index), flushed=True))
    pending.clear()
    await out.close()


async def broadcast_frames[FrameT, DetectionT](
    in_: Channel[AnnotatedFrame[FrameT, DetectionT]],
    broadcaster: Broadcaster[AnnotatedFrame[FrameT, DetectionT]],
) -> None:
    async for annotated in in_:
        await broadcaster.push_frame(annotated)


# ──────────────────────────────────────────────────────────────────────────
# main(): wire the stages, run to EOS, check the invariants
# ──────────────────────────────────────────────────────────────────────────


async def main():
    fps = 20.0
    frame_count = 100
    batch_frames = 4
    lookahead = 2

    async with AsyncExitStack() as stack:
        source = await stack.enter_async_context(
            NumberSource.start(count=frame_count, fps=fps)
        )
        broadcaster: Broadcaster[AnnotatedFrame[int, int]] = (
            await stack.enter_async_context(Broadcaster.start())
        )
        detector = await stack.enter_async_context(DivisorDetector.start(3))

        frame_channel: Channel[Frame[int]] = Channel(maxsize=16)
        annotated_channel: Channel[Frame[int] | Snapshot[int]] = Channel(maxsize=32)
        output_channel: Channel[AnnotatedFrame[int, int]] = Channel(maxsize=16)

        gate = BatchGate(fps=fps, batch_frames=batch_frames, max_batch_lag_ms=100.0)

        await asyncio.gather(
            produce_frames(source, frame_channel),
            detect_frames(frame_channel, annotated_channel, detector, gate),
            render_frames(annotated_channel, output_channel, lookahead),
            broadcast_frames(output_channel, broadcaster),
        )

        # ── invariants ────────────────────────────────────────────────────
        emitted_indices = [annotated.frame.index for annotated in broadcaster.received]
        assert emitted_indices == list(range(frame_count)), (
            f"frames must be emitted exactly once, in order: {emitted_indices}"
        )

        for annotated in broadcaster.received:
            if annotated.flushed:
                continue
            assert annotated.bracket is not None, (
                f"non-flushed frame {annotated.frame.index} has no bracket"
            )
            earlier, later = annotated.bracket
            assert earlier.frame_index <= annotated.frame.index <= later.frame_index, (
                f"frame {annotated.frame.index} outside its bracket "
                f"[{earlier.frame_index}, {later.frame_index}]"
            )

        assert 2 <= detector.max_batch_seen <= batch_frames, (
            f"batching should happen within the cap "
            f"(max batch seen: {detector.max_batch_seen})"
        )

        exact = sum(1 for a in broadcaster.received if a.is_exact)
        interpolated = sum(
            1 for a in broadcaster.received if not a.is_exact and not a.flushed
        )
        flushed = sum(1 for a in broadcaster.received if a.flushed)
        logger.info(
            f"all checks passed: {frame_count} frames emitted "
            f"({exact} exact, {interpolated} interpolated, {flushed} flushed at EOS), "
            f"{len(detector.sampled_payloads)} frames detected in batches of "
            f"<= {detector.max_batch_seen}"
        )


if __name__ == "__main__":
    asyncio.run(main())

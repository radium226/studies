#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy>=1.26",
#     "scipy>=1.11",
# ]
# ///
"""Generic, single-file condensation of the video-streamer pipeline core.

Derived from the app's architecture (see app/src/video_streamer/ and
README.md), distilled to its load-bearing algorithms and made generic over
the frame, detection, rendered-output and broadcast-payload types. Every
concrete concern — ffmpeg, ONNX inference, ISO BMFF parsing, HTTP — becomes
a small Protocol seam.

    FrameSource ──> Engine ──────────────────────> Encoder ──> Broadcaster
       (frames)      │  buffer pristine frames        (frames    (fan-out to
                     │  batched, throttled detection   in, init+  many async
                     │  (off-thread) -> tracking       fragments  clients with
                     │  lookahead spline interpolation out)       drop-oldest
                     │  overlay onto a DELAYED frame              retention)
                     └── all wired by an Orchestrator (which pluggable
                         StopConditions can end early, gracefully), whose
                         lifecycle is owned by a PipelineManager
                         (idle <-> playing).

Read top-down: vocabulary -> protocols -> small utilities (lifecycle,
token bucket, stopwatch, metrics) -> the core (interpolation, broadcast,
engine + batch gate, stop conditions, orchestration, lifecycle manager)
-> a default tracker -> a synthetic demo world -> `main()`, which runs the
whole stack end to end on fake data and asserts the pipeline's key
invariants.

Run it:  uv run ./pipeline.py   (takes ~9 s, exits 0 iff every check passes)
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from bisect import bisect_left
from collections import deque
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Generator,
    Iterable,
    Sequence,
)
from concurrent.futures import ThreadPoolExecutor
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
    nullcontext,
    suppress,
)
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, Self, TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline, PchipInterpolator

logger = logging.getLogger("pipeline")

type InterpolationMethod = Literal["cubic", "pchip", "linear"]


# ──────────────────────────────────────────────────────────────────────────
# Generic vocabulary
# ──────────────────────────────────────────────────────────────────────────


class Interpolatable(Protocol):
    """A detection whose numeric state can round-trip through a vector.

    The lookahead buffer splines *vectors*; it neither knows nor cares what
    the coordinates mean. `with_vector` rebuilds a detection from the
    interpolated vector using `self` as the template, so non-numeric fields
    (confidence, labels, ...) carry over from the segment-start detection.
    """

    def to_vector(self) -> NDArray[np.float64]: ...

    def with_vector(self, vector: NDArray[np.float64]) -> Self: ...


class HasBBox(Protocol):
    """What the default (bbox-flavoured) tracker needs from a detection."""

    @property
    def bbox(self) -> tuple[float, float, float, float]: ...

    @property
    def confidence(self) -> float: ...


@dataclass(frozen=True)
class Tracked[DetectionT]:
    """One tracked object: a stable id + the matched *input* detection.

    `detection` is always the raw detection the tracker associated, never an
    internal (e.g. Kalman) estimate — estimates drift from the measurement,
    and downstream consumers need coordinates consistent with the detector's
    output. `embedding` is the most recent appearance vector for this id.
    """

    track_id: int
    detection: DetectionT
    embedding: NDArray[np.float32] | None


# ──────────────────────────────────────────────────────────────────────────
# Component protocols (the pluggable seams)
# ──────────────────────────────────────────────────────────────────────────


class FrameSource[FrameT](Protocol):
    """~ InputVideoLoader: paces out pristine frames at some native fps."""

    @property
    def fps(self) -> float: ...

    def frames(self) -> AsyncIterator[FrameT]: ...


class Detector[FrameT, DetectionT](Protocol):
    """~ FaceDetector.detect_batch: one batched pass over N frames.

    Synchronous ON PURPOSE: the Engine calls it via run_in_executor, exactly
    like the real ONNX session — inference is the only place work leaves the
    event loop. Returns one detection list per input frame, same order.
    """

    def detect_batch(self, frames: Sequence[FrameT]) -> list[list[DetectionT]]: ...


class Embedder[FrameT, DetectionT](Protocol):
    """~ FaceEmbedder.embed_many: one appearance vector per (frame, detection) pair.

    Must chunk internally at <= max_batch items per underlying pass, and
    return exactly one embedding per input item, in order. Synchronous for
    the same executor reason as Detector.
    """

    def embed_many(
        self, items: Sequence[tuple[FrameT, DetectionT]], max_batch: int
    ) -> list[NDArray[np.float32]]: ...


class Tracker[DetectionT](Protocol):
    """~ ByteTracker wrapper: detections in, stable-id Tracked objects out.

    Called once per *sampled* frame in ascending frame order (detection
    cadence, not video fps). Implementations must return the matched input
    detections (see Tracked) and persist embeddings per track id.
    """

    def update(
        self, detections: list[DetectionT], embeddings: list[NDArray[np.float32]]
    ) -> list[Tracked[DetectionT]]: ...


class Overlay[FrameT, DetectionT, OutT](Protocol):
    """~ draw_overlay + Engine._draw_detections, collapsed into one seam.

    CONTRACT: must return a NEW object and never mutate `frame`. The Engine
    keeps the pristine frame in its delay buffer (it may be re-emitted while
    the render cursor is clamped) and the detector must never see burned-in
    overlays — the real code enforces this with a single frame.copy() before
    drawing; here the copy is the renderer's responsibility.
    """

    def render(
        self,
        frame: FrameT,
        caption: str,
        tracked: Sequence[Tracked[DetectionT]],
        is_interpolated: bool,
    ) -> OutT: ...


@dataclass(frozen=True)
class EncoderOutput[PayloadT]:
    """One unit of encoder output: the init segment or a media fragment.

    Mirrors the fMP4 split the real box-parse task performs: everything up
    to and including moov is the init segment, then each moof+mdat pair is
    one self-contained fragment.
    """

    kind: Literal["init", "fragment"]
    payload: PayloadT


class Encoder[OutT, PayloadT](Protocol):
    """~ Writer (ffmpeg encoder) + BoxReader, collapsed into one seam:
    rendered frames in, discrete init/fragment payloads out."""

    async def write_frame(self, frame: OutT) -> None: ...

    async def close_input(self) -> None:
        """EOF the input side; the encoder must flush trailing output and
        then signal EOF from read_output (ffmpeg's close-stdin contract)."""
        ...

    async def read_output(self) -> EncoderOutput[PayloadT] | None:
        """Next output unit, or None once the encoder has flushed and ended."""
        ...


# ──────────────────────────────────────────────────────────────────────────
# Small utilities: lifecycle ownership and timing
# ──────────────────────────────────────────────────────────────────────────


class SupportsAclose(Protocol):
    async def aclose(self) -> None: ...


@asynccontextmanager
async def running[T: SupportsAclose](component: T) -> AsyncGenerator[T]:
    """Own `component` for the duration of the context; aclose it on exit."""
    try:
        yield component
    finally:
        await component.aclose()


@dataclass
class Timer:
    seconds: float = 0.0


@contextmanager
def stopwatch(clock: Callable[[], float]) -> Generator[Timer]:
    """Measure the wall-clock duration of the enclosed block."""
    timer = Timer()
    started = clock()
    try:
        yield timer
    finally:
        timer.seconds = clock() - started


# ──────────────────────────────────────────────────────────────────────────
# TokenBucket and BatchGate — the detection throttle
# ──────────────────────────────────────────────────────────────────────────


class TokenBucket:
    """Generic token bucket: refills over wall-clock time, capped at capacity.

    Gate-then-spend, not reserve-then-adjust: `try_acquire` only reports
    whether enough budget is currently available; the caller reports how much
    was actually used afterward via `record_spend`. This fits work whose cost
    isn't known until after it runs (inference duration varies per machine).
    """

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
    """Decides when a detection batch may fire.

    Two gates compose. A TokenBucket (measured in frame-budget units:
    detection seconds x fps) adapts detection frequency to however long
    inference actually takes on this machine. On top of it, a wait-or-cap
    accumulation policy holds an eligible batch until it is full
    (`batch_frames` frames available) or `max_batch_lag_ms` has elapsed
    since it first became eligible — whichever comes first;
    `max_batch_lag_ms=0` fires immediately.

    `should_fire` is called once per video frame; `reset` clears the
    accumulation timer whenever eligibility is lost (a batch already in
    flight, no budget, nothing to sample).
    """

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
# MetricsCollector
# ──────────────────────────────────────────────────────────────────────────


class Window:
    """Trailing time-window of `(timestamp, value)` samples."""

    def __init__(self, window_seconds: float, clock: Callable[[], float]) -> None:
        self.window_seconds = window_seconds
        self.clock = clock
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, value: float) -> None:
        now = self.clock()
        self.samples.append((now, value))
        self.evict(now)

    def evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def mean(self) -> float:
        self.evict(self.clock())
        if not self.samples:
            return 0.0
        return sum(v for _, v in self.samples) / len(self.samples)

    def rate(self) -> float:
        """Samples per second, averaged over the window (a per-second count)."""
        self.evict(self.clock())
        if not self.samples:
            return 0.0
        return len(self.samples) / self.window_seconds


class MetricsCollector:
    """Live pipeline metrics as trailing time-window moving averages.

    Everything is recorded and read on the event loop (Engine.process and
    whatever surfaces the snapshot), so there is no cross-thread access and
    no locking. Averages/rates cover the last `window_seconds`, tracking
    *current* behaviour rather than a lifetime mean.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_seconds = window_seconds
        self.detections_per_frame = Window(window_seconds, clock)
        self.detection_duration_ms = Window(window_seconds, clock)
        self.embedding_duration_ms = Window(window_seconds, clock)
        self.batch_duration_ms = Window(window_seconds, clock)
        self.completed_batches = Window(window_seconds, clock)
        self.processed_frames = Window(window_seconds, clock)
        self.detected_frames = Window(window_seconds, clock)
        self.detection_batch_size = Window(window_seconds, clock)
        self.embedding_batch_size = Window(window_seconds, clock)
        self.active_tracks = 0

    def record_processed_frame(self) -> None:
        self.processed_frames.add(1.0)

    def record_detection_batch(
        self,
        batch: BatchResult[Any],
        *,
        detection_counts: Iterable[int],
        active_tracks: int,
    ) -> None:
        self.detection_duration_ms.add(batch.detection_seconds * 1000.0)
        self.embedding_duration_ms.add(batch.embedding_seconds * 1000.0)
        self.batch_duration_ms.add(
            (batch.detection_seconds + batch.embedding_seconds) * 1000.0
        )
        self.completed_batches.add(1.0)
        self.detection_batch_size.add(float(batch.detected_frame_count))
        self.embedding_batch_size.add(float(batch.embedded_crop_count))
        for count in detection_counts:
            self.detections_per_frame.add(float(count))
            # One sample per frame actually run through detection, so its
            # rate is detected-frames/sec — same units as processed fps.
            self.detected_frames.add(1.0)
        self.active_tracks = active_tracks

    def snapshot(self) -> dict[str, float]:
        # Stride: processed frames per frame actually detected — how many
        # video frames the interpolator covers for each real detection.
        detected_fps = self.detected_frames.rate()
        processed_fps = self.processed_frames.rate()
        stride = processed_fps / detected_fps if detected_fps > 0 else 0.0
        return {
            "detections_per_frame": round(self.detections_per_frame.mean(), 2),
            "detection_ms": round(self.detection_duration_ms.mean(), 1),
            "embedding_ms": round(self.embedding_duration_ms.mean(), 1),
            "batch_duration_ms": round(self.batch_duration_ms.mean(), 1),
            "detection_hz": round(self.completed_batches.rate(), 2),
            "detection_stride": round(stride, 1),
            "processed_fps": round(processed_fps, 1),
            "active_tracks": self.active_tracks,
            "detect_batch_size": round(self.detection_batch_size.mean(), 1),
            "embed_batch_size": round(self.embedding_batch_size.mean(), 1),
            "window_s": self.window_seconds,
        }


# ──────────────────────────────────────────────────────────────────────────
# LookaheadTrackBuffer — cf. app's interpolation.py
# ──────────────────────────────────────────────────────────────────────────


def interpolate_vectors(
    control_frame_indices: NDArray[np.float64],
    control_vectors: NDArray[np.float64],
    query_frame_index: float,
    method: InterpolationMethod,
) -> NDArray[np.float64]:
    if method == "pchip":
        return PchipInterpolator(control_frame_indices, control_vectors)(
            query_frame_index
        )
    if method == "linear":
        return np.array(
            [
                np.interp(
                    query_frame_index, control_frame_indices, control_vectors[:, dim]
                )
                for dim in range(control_vectors.shape[1])
            ]
        )
    return CubicSpline(control_frame_indices, control_vectors)(query_frame_index)


@dataclass
class TrackSnapshot[DetectionT]:
    frame_index: int
    tracks: dict[int, Tracked[DetectionT]]  # keyed by track_id


@dataclass(frozen=True)
class InterpolatedFrame[DetectionT]:
    """One render-cursor step: the (past) frame the coordinates belong to.

    is_interpolated is True when the frame lies strictly between two
    detection snapshots, False when it lands exactly on one.
    """

    frame_index: int
    tracks: list[Tracked[DetectionT]]
    is_interpolated: bool


class LookaheadTrackBuffer[DetectionT: Interpolatable]:
    """Per-video-frame spline interpolation of tracked detections with a
    fixed lookahead lag.

    Detection runs much less often than the video frame rate, so this class
    maintains a render cursor that advances independently of the live frame
    index. The cursor always stays within a detection segment that has
    `lookahead` future snapshots available, guaranteeing pure interpolation
    (never extrapolation).

    Tracks are matched across snapshots by tracker id, not list position —
    detection order is typically confidence order, so positional matching
    would blend coordinates of different objects whenever confidence ranks
    flip or an object enters/leaves the frame.

    Because of the lookahead, the returned coordinates describe a frame
    several detection intervals in the past. The caller must delay the video
    frames by the same amount (`get()` returns the frame index to pair with)
    or the overlay will trail moving objects.
    """

    def __init__(
        self, lookahead: int = 3, method: InterpolationMethod = "pchip"
    ) -> None:
        self.lookahead = lookahead
        self.method = method
        self.snapshots: list[TrackSnapshot[DetectionT]] = []
        # Index of the current segment's start snapshot.
        self.segment_start_index: int = 0
        self.render_cursor: float | None = None

    def push(self, frame_index: int, tracks: list[Tracked[DetectionT]]) -> None:
        self.snapshots.append(
            TrackSnapshot(frame_index, {t.track_id: t for t in tracks})
        )

    @property
    def ready(self) -> bool:
        # Need the segment end (segment_start_index + 1) plus `lookahead`
        # more snapshots ahead of it.
        return len(self.snapshots) > self.segment_start_index + self.lookahead + 1

    def get(self) -> InterpolatedFrame[DetectionT] | None:
        """Return the interpolated tracks for the current render cursor.

        Each call advances the internal render cursor by one frame. Returns
        None while the buffer is still accumulating its initial lookahead.
        The frame_index names the (past) video frame the coordinates belong
        to; the caller should render onto that frame, not the live one.
        """
        if not self.ready:
            return None
        render_frame_index, is_interpolated = self.advance_cursor()
        tracks = self.interpolate_tracks(render_frame_index)
        return InterpolatedFrame(render_frame_index, tracks, is_interpolated)

    def advance_cursor(self) -> tuple[int, bool]:
        """Step the render cursor one frame; returns
        (render_frame_index, is_interpolated).

        Also advances the current segment, drops snapshots the spline window
        can never reach again, and caps the cursor so it can't outrun the
        available lookahead.
        """
        segment_start_snapshot = self.snapshots[self.segment_start_index]
        segment_end_snapshot = self.snapshots[self.segment_start_index + 1]

        if self.render_cursor is None:
            self.render_cursor = float(segment_start_snapshot.frame_index)

        # Clamp to current segment for pure interpolation.
        render_frame_index = int(
            np.clip(
                self.render_cursor,
                segment_start_snapshot.frame_index,
                segment_end_snapshot.frame_index,
            )
        )
        is_interpolated = render_frame_index not in (
            segment_start_snapshot.frame_index,
            segment_end_snapshot.frame_index,
        )
        self.render_cursor += 1.0

        # Advance to the next segment once the cursor leaves the current
        # one, but only when we still have enough lookahead beyond the new
        # segment end.
        while (
            self.render_cursor
            > self.snapshots[self.segment_start_index + 1].frame_index
            and len(self.snapshots) > self.segment_start_index + self.lookahead + 2
        ):
            self.segment_start_index += 1

        # Snapshots behind the spline window can never be used again.
        drop_count = self.segment_start_index - self.lookahead
        if drop_count > 0:
            del self.snapshots[:drop_count]
            self.segment_start_index -= drop_count

        # Cap the cursor at the newest frame we can still interpolate (the
        # one with `lookahead` snapshots after it). Detection results arrive
        # in bursts — many snapshots at once, then a gap — so without this
        # cap the cursor free-runs during the gap (render_frame_index is
        # clamped, but render_cursor keeps incrementing), accruing "debt"
        # that lurches forward when the next burst lands: a visible overlay
        # jump. Holding the cursor here instead makes it resume smoothly,
        # one frame at a time.
        newest_renderable_index = len(self.snapshots) - self.lookahead - 1
        if newest_renderable_index >= 1:
            cursor_cap = float(self.snapshots[newest_renderable_index].frame_index)
            if self.render_cursor > cursor_cap:
                self.render_cursor = cursor_cap

        return render_frame_index, is_interpolated

    def interpolate_tracks(self, render_frame_index: int) -> list[Tracked[DetectionT]]:
        """Spline-interpolate every track of the current segment start at
        render_frame_index, matching control points across snapshots by track id."""
        segment_start_snapshot = self.snapshots[self.segment_start_index]

        # Build the spline window: up to `lookahead` snapshots on each side
        # of the segment being rendered.
        window_start = max(0, self.segment_start_index - self.lookahead)
        window_end = min(
            len(self.snapshots), self.segment_start_index + self.lookahead + 2
        )
        window_snapshots = self.snapshots[window_start:window_end]

        tracks: list[Tracked[DetectionT]] = []
        for track_id, tracked in segment_start_snapshot.tracks.items():
            control_points = [
                (snapshot.frame_index, snapshot.tracks[track_id])
                for snapshot in window_snapshots
                if track_id in snapshot.tracks
            ]
            if len(control_points) < 2:
                # A single control point can't be splined; emit it raw.
                tracks.append(tracked)
                continue

            control_frame_indices = np.array(
                [frame_index for frame_index, _ in control_points],
                dtype=np.float64,
            )
            control_vectors = np.stack(
                [point.detection.to_vector() for _, point in control_points]
            ).astype(np.float64)
            interpolated_vector = interpolate_vectors(
                control_frame_indices,
                control_vectors,
                float(render_frame_index),
                self.method,
            )

            # The segment-start detection is the template: with_vector swaps
            # in the interpolated numeric state, everything else carries over.
            tracks.append(
                Tracked(
                    track_id=track_id,
                    detection=tracked.detection.with_vector(interpolated_vector),
                    embedding=tracked.embedding,
                )
            )

        return tracks


# ──────────────────────────────────────────────────────────────────────────
# Broadcaster — cf. app's broadcaster.py
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fragment[PayloadT]:
    sequence_number: int
    payload: PayloadT


@dataclass(frozen=True)
class BroadcastSnapshot[PayloadT]:
    init_segment: PayloadT | None
    fragment: Fragment[PayloadT] | None


class LaggedError(Exception):
    def __init__(self, oldest_retained_sequence: int) -> None:
        super().__init__(
            "subscriber fell behind retained fragments "
            f"(oldest retained sequence_number={oldest_retained_sequence})"
        )
        self.oldest_retained_sequence = oldest_retained_sequence


class Broadcaster[PayloadT]:
    """Async fan-out of a live stream to many subscribers.

    The encode-read task is the sole producer; client handlers (async tasks
    on the same event loop) are consumers, awaiting `wait_for_next` directly
    via an `asyncio.Condition` — no thread bridging needed since producer
    and consumers already share the event loop.

    A bounded deque gives an automatic, allocation-free drop-oldest
    retention policy: total memory is capped regardless of subscriber count
    or speed.
    """

    def __init__(self, max_fragments: int = 15) -> None:
        self.condition = asyncio.Condition()
        self.init_segment: PayloadT | None = None
        self.fragments: deque[Fragment[PayloadT]] = deque(maxlen=max_fragments)
        self.next_sequence_number = 0
        self.closed = False

    async def set_init_segment(self, payload: PayloadT) -> None:
        async with self.condition:
            self.init_segment = payload
            self.condition.notify_all()

    async def publish_fragment(self, payload: PayloadT) -> None:
        async with self.condition:
            fragment = Fragment(
                sequence_number=self.next_sequence_number, payload=payload
            )
            self.next_sequence_number += 1
            self.fragments.append(fragment)
            self.condition.notify_all()

    @property
    def is_closed(self) -> bool:
        return self.closed

    def snapshot_for_new_client(self) -> BroadcastSnapshot[PayloadT]:
        """Init segment + only the single latest fragment (true live, not
        rewind).

        No locking needed: asyncio is single-threaded/cooperative and this
        method never awaits, so it can't interleave with a concurrent
        publish_fragment.
        """
        latest = self.fragments[-1] if self.fragments else None
        return BroadcastSnapshot(init_segment=self.init_segment, fragment=latest)

    async def wait_for_next(
        self, last_seen_sequence: int, timeout: float
    ) -> Fragment[PayloadT] | None:
        """Wait until a fragment newer than last_seen_sequence exists, or
        timeout.

        Returns None on timeout/no-new-data/closed. Raises LaggedError if
        the caller's last_seen_sequence has fallen behind the oldest
        retained fragment — callers should treat that as "can't catch up,
        end this connection."
        """
        async with self.condition:
            try:
                async with asyncio.timeout(timeout):
                    await self.condition.wait_for(
                        lambda: self.closed or self.has_next(last_seen_sequence)
                    )
            except TimeoutError:
                return None
            if self.closed:
                return None
            oldest_retained_sequence = self.fragments[0].sequence_number
            if last_seen_sequence < oldest_retained_sequence - 1:
                raise LaggedError(oldest_retained_sequence)
            # Sequence numbers are contiguous, so the next unseen fragment's
            # position in the deque follows from its sequence number.
            return self.fragments[
                max(0, last_seen_sequence + 1 - oldest_retained_sequence)
            ]

    def has_next(self, last_seen_sequence: int) -> bool:
        return (
            bool(self.fragments)
            and self.fragments[-1].sequence_number > last_seen_sequence
        )

    async def close(self) -> None:
        async with self.condition:
            self.closed = True
            self.condition.notify_all()

    # Closing is also this component's teardown, so `running()` can own it.
    aclose = close

    async def wait_closed(self) -> None:
        async with self.condition:
            await self.condition.wait_for(lambda: self.closed)


# ──────────────────────────────────────────────────────────────────────────
# Engine — cf. app's engine.py
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineConfig:
    """Global tuning knobs (the CLI flags of the real app).

    batch_frames    — max frames per batched detection pass (--scrfd-batch-frames)
    embed_max_batch — max crops per batched embedding pass (--arcface-batch-crops)
    max_batch_lag_ms — wait-or-cap batch accumulation, see BatchGate
    lookahead       — render-cursor lag in detection snapshots (--lookahead)
    max_pending_frames — cap on frames held back for the render cursor; the
                       normal steady-state backlog is (lookahead + 2)
                       detection intervals' worth, so this only bites when
                       detection stalls badly
    """

    batch_frames: int = 4
    embed_max_batch: int = 8
    max_batch_lag_ms: float = 0.0
    lookahead: int = 3
    method: InterpolationMethod = "pchip"
    max_pending_frames: int = 600


@dataclass(frozen=True)
class PendingFrame[FrameT]:
    """A pristine frame held back until the render cursor reaches it."""

    frame: FrameT
    captured_at: str


@dataclass(frozen=True)
class SampledFrame[FrameT]:
    """One pristine frame picked for a detection batch, with its index."""

    frame_index: int
    frame: FrameT


@dataclass(frozen=True)
class FrameDetections[DetectionT]:
    """Detections + matching embeddings for one sampled frame."""

    frame_index: int
    detections: list[DetectionT]
    embeddings: list[NDArray[np.float32]]


@dataclass(frozen=True)
class BatchResult[DetectionT]:
    """One detection pass: per-frame results plus how long each stage took."""

    per_frame_detections: list[FrameDetections[DetectionT]]
    detection_seconds: float
    embedding_seconds: float
    detected_frame_count: int  # frames submitted to the detector
    embedded_crop_count: int  # detections submitted to the embedder


class Engine[FrameT, DetectionT: Interpolatable, OutT]:
    """The CV heart: sparse batched detection, tracking, interpolation, and
    delayed emission — decoupled from the video frame rate.

    `process()` is a four-phase outline: buffer the pristine frame, harvest
    a finished detection batch, maybe schedule the next one, and emit the
    (delayed, overlaid) frame the render cursor points at.
    """

    def __init__(
        self,
        *,
        detector: Detector[FrameT, DetectionT],
        embedder: Embedder[FrameT, DetectionT],
        tracker: Tracker[DetectionT],
        overlay: Overlay[FrameT, DetectionT, OutT],
        fps: float,
        config: EngineConfig = EngineConfig(),
        clock: Callable[[], float] = time.monotonic,
        on_tracks: Callable[[int, Sequence[Tracked[DetectionT]]], None]
        | None = None,
    ) -> None:
        self.detector = detector
        self.embedder = embedder
        self.tracker = tracker
        self.overlay = overlay
        self.clock = clock
        # Neutral tracker-output hook (sampled frame index + tracked
        # objects), fired once per sampled frame as batches complete. The
        # Engine stays ignorant of what listens — the manager adapts it
        # into stop-condition events.
        self.on_tracks = on_tracks
        self.embed_max_batch = max(1, config.embed_max_batch)
        self.max_pending_frames = config.max_pending_frames
        self.frame_index = 0
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.detection_buffer: LookaheadTrackBuffer[DetectionT] = LookaheadTrackBuffer(
            lookahead=config.lookahead, method=config.method
        )
        self.batch_gate = BatchGate(
            fps=fps,
            batch_frames=config.batch_frames,
            max_batch_lag_ms=config.max_batch_lag_ms,
            clock=clock,
        )
        self.detection_task: asyncio.Future[BatchResult[DetectionT]] | None = None
        self.detection_started_at: float = 0.0
        # Newest frame index covered by the most recently scheduled batch;
        # the next batch samples the frames after it, giving gapless coverage.
        self.newest_batched_frame_index: int = 0
        self.metrics = MetricsCollector(clock=clock)
        # Frames awaiting emission, keyed by frame index. The interpolation
        # buffer's render cursor trails the live frame by several detection
        # intervals; overlays are drawn on the frame they were computed for,
        # so frames are held back here until the cursor reaches them.
        self.pending_frames: dict[int, PendingFrame[FrameT]] = {}
        # True while emitting box-less fallback frames after a cap eviction;
        # gates the warning so a stall logs once, not once per frame.
        self.fallback_active = False

    async def process(self, frame: FrameT) -> OutT | None:
        """Feed one live frame in; get the (delayed) overlaid frame out.

        Returns None while the lookahead buffer is still filling — the
        caller should simply skip writing in that case.
        """
        self.frame_index += 1
        self.metrics.record_processed_frame()
        self.buffer_frame(frame)
        self.collect_finished_batch()
        self.maybe_schedule_batch()
        return self.emit_delayed_frame()

    def buffer_frame(self, frame: FrameT) -> None:
        """Hold the pristine frame back until the render cursor reaches it."""
        captured_at = datetime.now(UTC).strftime("%H:%M:%S")
        self.pending_frames[self.frame_index] = PendingFrame(frame, captured_at)
        if len(self.pending_frames) > self.max_pending_frames:
            evicted_frame_index = next(iter(self.pending_frames))
            del self.pending_frames[evicted_frame_index]
            logger.warning(
                "pending-frame buffer full, dropped frame %s — detection is "
                "falling behind",
                evicted_frame_index,
            )

    def collect_finished_batch(self) -> None:
        """Harvest a completed detection pass, if any.

        Tracking runs here, on the raw detections, so track ids are assigned
        before interpolation and the spline control points for an object all
        belong to that object.
        """
        if self.detection_task is None or not self.detection_task.done():
            return
        self.batch_gate.record_spend(self.clock() - self.detection_started_at)
        if not self.detection_task.cancelled():
            try:
                # One result per sampled frame, in ascending frame order.
                # Feed the tracker frame by frame so ids stay stable, and
                # stamp each snapshot with the frame it ran on (not the
                # later frame it finished on) or the interpolation timeline
                # shifts forward and boxes trail moving objects.
                batch_result = self.detection_task.result()
                detection_counts: list[int] = []
                active_tracks = 0
                for frame_detections in batch_result.per_frame_detections:
                    tracked = self.tracker.update(
                        frame_detections.detections, frame_detections.embeddings
                    )
                    self.detection_buffer.push(
                        frame_detections.frame_index, tracked
                    )
                    if self.on_tracks is not None:
                        self.on_tracks(frame_detections.frame_index, tracked)
                    detection_counts.append(len(frame_detections.detections))
                    active_tracks = len(tracked)
                self.metrics.record_detection_batch(
                    batch_result,
                    detection_counts=detection_counts,
                    active_tracks=active_tracks,
                )
            except Exception:
                logger.warning("detection failed", exc_info=True)
        self.detection_task = None

    def maybe_schedule_batch(self) -> None:
        """Kick off a new detection batch if none is already running and
        the BatchGate (budget + wait-or-cap accumulation) allows it."""
        if self.detection_task is not None:
            self.batch_gate.reset()
            return
        # Approximate window size (precise intersection with pending_frames
        # is done inside sample_batch_frames).
        available_frames = self.frame_index - self.newest_batched_frame_index
        if not self.batch_gate.should_fire(available_frames):
            return
        sampled_frames = self.sample_batch_frames()
        if not sampled_frames:
            return
        self.detection_started_at = self.clock()
        self.newest_batched_frame_index = sampled_frames[-1].frame_index
        loop = asyncio.get_running_loop()
        self.detection_task = loop.run_in_executor(
            self.executor, self.detect_batch, sampled_frames
        )

    def emit_delayed_frame(self) -> OutT | None:
        """Advance the render cursor and emit the frame it points at,
        overlaid with that frame's (interpolated) detections. None while the
        lookahead buffer is still filling."""
        interpolated = self.detection_buffer.get()
        if interpolated is None:
            return None
        render_frame_index = interpolated.frame_index
        tracks = interpolated.tracks
        is_interpolated = interpolated.is_interpolated

        # Emit the frame the coordinates were computed for. Frames older
        # than render_frame_index are dropped; render_frame_index itself is
        # kept because the cursor re-emits it while clamped waiting for
        # lookahead.
        while self.pending_frames:
            oldest_frame_index = next(iter(self.pending_frames))
            if oldest_frame_index >= render_frame_index:
                break
            del self.pending_frames[oldest_frame_index]
        pending_frame = self.pending_frames.get(render_frame_index)
        if pending_frame is None:
            # render_frame_index was evicted by the max_pending_frames cap
            # (detection is stalling badly). Emit the oldest surviving frame
            # without boxes rather than drawing render_frame_index's
            # coordinates on the wrong frame.
            if not self.pending_frames:
                return None
            if not self.fallback_active:
                self.fallback_active = True
                logger.warning(
                    "frame %s already evicted; emitting frames without boxes "
                    "until the render cursor catches up",
                    render_frame_index,
                )
            pending_frame = self.pending_frames[next(iter(self.pending_frames))]
            tracks = []
        else:
            self.fallback_active = False

        # The pristine original stays in pending_frames (render_frame_index
        # is re-emitted while the cursor is clamped, and the detector must
        # never see burned-in overlays) — render() must return a new object.
        return self.overlay.render(
            pending_frame.frame,
            f"{pending_frame.captured_at}  frame {render_frame_index}",
            tracks,
            is_interpolated,
        )

    def sample_batch_frames(self) -> list[SampledFrame[FrameT]]:
        """Pick up to N evenly spaced frames buffered since the last batch.

        Only frames still present in `pending_frames` are eligible (older
        ones may already have been emitted and dropped). The newest frame is
        always included so consecutive batches stay contiguous.
        """
        candidate_frame_indices = [
            frame_index
            for frame_index in range(
                self.newest_batched_frame_index + 1, self.frame_index + 1
            )
            if frame_index in self.pending_frames
        ]
        if not candidate_frame_indices:
            return []
        sample_count = min(
            self.batch_gate.batch_frames, len(candidate_frame_indices)
        )
        sample_positions = np.linspace(
            0, len(candidate_frame_indices) - 1, sample_count
        )
        chosen_frame_indices = sorted(
            {
                candidate_frame_indices[round(float(position))]
                for position in sample_positions
            }
        )
        return [
            SampledFrame(frame_index, self.pending_frames[frame_index].frame)
            for frame_index in chosen_frame_indices
        ]

    def detect_batch(
        self, sampled_frames: list[SampledFrame[FrameT]]
    ) -> BatchResult[DetectionT]:
        """Runs on the single-worker executor thread, never the event loop."""
        frames = [sample.frame for sample in sampled_frames]
        with stopwatch(self.clock) as detection_timer:
            detections_per_frame = self.detector.detect_batch(frames)

        # Flatten every (frame, detection) pair into one embedding batch,
        # then split the embeddings back per source frame by count.
        embedding_items: list[tuple[FrameT, DetectionT]] = [
            (frame, detection)
            for frame, detections in zip(frames, detections_per_frame, strict=True)
            for detection in detections
        ]
        with stopwatch(self.clock) as embedding_timer:
            embeddings = self.embedder.embed_many(
                embedding_items, max_batch=self.embed_max_batch
            )

        per_frame_detections: list[FrameDetections[DetectionT]] = []
        embedding_offset = 0
        for sample, detections in zip(
            sampled_frames, detections_per_frame, strict=True
        ):
            per_frame_detections.append(
                FrameDetections(
                    sample.frame_index,
                    detections,
                    embeddings[
                        embedding_offset : embedding_offset + len(detections)
                    ],
                )
            )
            embedding_offset += len(detections)
        return BatchResult(
            per_frame_detections=per_frame_detections,
            detection_seconds=detection_timer.seconds,
            embedding_seconds=embedding_timer.seconds,
            detected_frame_count=len(frames),
            embedded_crop_count=len(embedding_items),
        )

    def metrics_snapshot(self) -> dict[str, float]:
        return self.metrics.snapshot()

    async def aclose(self) -> None:
        # Cancel the in-flight future before shutting the executor down, or
        # a blocking detector call would hang teardown.
        if self.detection_task is not None and not self.detection_task.done():
            self.detection_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.detection_task
        self.executor.shutdown(wait=False, cancel_futures=True)


# ──────────────────────────────────────────────────────────────────────────
# Stop conditions — pluggable early-stop strategies
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrameRead:
    """One live frame was read from the source (the live edge, before any
    Engine processing — 'frames read', not 'frames emitted')."""

    frame_index: int


@dataclass(frozen=True)
class TracksUpdated[DetectionT]:
    """The tracker ran on one sampled frame (detection cadence, not video
    fps)."""

    frame_index: int
    tracks: Sequence[Tracked[DetectionT]]


type PipelineEvent[DetectionT] = FrameRead | TracksUpdated[DetectionT]


class StopCondition[DetectionT](Protocol):
    """An external reason to stop a running pipeline.

    Push-based: the pipeline feeds every condition the typed events it
    emits (a frame read from the source, a tracker update); a condition
    answers with a human-readable stop reason to end the stream, or None
    to keep going. Conditions ignore event types they don't care about,
    and may be stateful (a frame counter, a deadline) — each build gets
    fresh instances via PipelineComponents.stop_condition_factories.

    A triggered stop is graceful, exactly like end-of-source: the frame
    loop stops feeding, the encoder flushes its trailing fragments, the
    broadcaster closes, and the manager goes idle — recording the reason
    as a StopRecord (distinct from an error) in its status.
    """

    def observe(self, event: PipelineEvent[DetectionT]) -> str | None: ...


class StopController[DetectionT]:
    """Any-of composition of stop conditions, owned per pipeline build.

    Feeds every event to every condition until one returns a reason; the
    first reason is latched (later events become no-ops) and read by the
    Orchestrator's frame loop and the manager watchdog.
    """

    def __init__(self, conditions: Sequence[StopCondition[DetectionT]]) -> None:
        self.conditions = conditions
        self.stop_reason: str | None = None

    @property
    def triggered(self) -> bool:
        return self.stop_reason is not None

    def observe(self, event: PipelineEvent[DetectionT]) -> None:
        if self.triggered:
            return
        for condition in self.conditions:
            reason = condition.observe(event)
            if reason is not None:
                self.stop_reason = reason
                return


class MaxFramesRead:
    """Stop once `max_frames` frames have been read from the source."""

    def __init__(self, max_frames: int) -> None:
        self.max_frames = max_frames
        self.frames_read = 0

    def observe(self, event: PipelineEvent[Any]) -> str | None:
        if not isinstance(event, FrameRead):
            return None
        self.frames_read += 1
        if self.frames_read >= self.max_frames:
            return f"read {self.frames_read} frames (limit {self.max_frames})"
        return None


class TracksMatch[DetectionT]:
    """Stop when a tracker update satisfies `predicate` — 'any track
    exists', 'track id 3 present', '>= 2 concurrent tracks' are all
    one-line predicates."""

    def __init__(
        self,
        predicate: Callable[[Sequence[Tracked[DetectionT]]], bool],
        description: str,
    ) -> None:
        self.predicate = predicate
        self.description = description

    def observe(self, event: PipelineEvent[DetectionT]) -> str | None:
        if isinstance(event, TracksUpdated) and self.predicate(event.tracks):
            return f"tracks matched: {self.description}"
        return None


class MaxDuration:
    """Stop after `seconds` of pipeline runtime, measured from the first
    event observed (not construction, so build latency doesn't count)."""

    def __init__(
        self, seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.seconds = seconds
        self.clock = clock
        self.started_at: float | None = None

    def observe(self, event: PipelineEvent[Any]) -> str | None:
        del event  # any event counts; only its timing matters
        now = self.clock()
        if self.started_at is None:
            self.started_at = now
            return None
        elapsed = now - self.started_at
        if elapsed >= self.seconds:
            return f"ran for {elapsed:.1f}s (limit {self.seconds:.1f}s)"
        return None


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator — cf. app's orchestrator.py
# ──────────────────────────────────────────────────────────────────────────


class Orchestrator[FrameT, DetectionT: Interpolatable, OutT, PayloadT]:
    """Wires FrameSource -> Engine -> Encoder -> Broadcaster together.

    Owns the two long-lived asyncio tasks that bridge those components: one
    shuttles frames from the source to the encoder through the Engine, the
    other reads the encoder's discrete output units and publishes them.
    """

    def __init__(
        self,
        source: FrameSource[FrameT],
        engine: Engine[FrameT, DetectionT, OutT],
        encoder: Encoder[OutT, PayloadT],
        broadcaster: Broadcaster[PayloadT],
        stop_controller: StopController[DetectionT] | None = None,
    ) -> None:
        self.source = source
        self.engine = engine
        self.encoder = encoder
        self.broadcaster = broadcaster
        self.stop_controller: StopController[DetectionT] = (
            stop_controller if stop_controller is not None else StopController(())
        )
        self.tasks: list[asyncio.Task[None]] = []
        self.failure_close_task: asyncio.Task[None] | None = None
        self.failure_exception: BaseException | None = None

    def start(self) -> Self:
        """Spawn the two bridge tasks.

        Plain create_task, not a TaskGroup: a TaskGroup held open for the
        pipeline's lifetime would, on a child crash, cancel whichever
        unrelated task happened to be inside it and park the exception
        until teardown. Crashes are surfaced immediately via on_task_done.
        """
        self.tasks = [
            asyncio.create_task(self.forward_frames(), name="frame-forward"),
            asyncio.create_task(self.read_encoder_output(), name="encode-read"),
        ]
        for task in self.tasks:
            task.add_done_callback(self.on_task_done)
        return self

    async def aclose(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.failure_close_task is not None:
            await self.failure_close_task

    @property
    def failure_reason(self) -> str | None:
        """Human-readable reason a pipeline task crashed, or None for a
        clean end (natural EOF). Read by the PipelineManager watchdog to
        decide whether going idle should surface a failure."""
        if self.failure_exception is None:
            return None
        return str(self.failure_exception)

    @property
    def stop_reason(self) -> str | None:
        """Reason a StopCondition ended the stream, or None. Read by the
        PipelineManager watchdog (after ruling out a crash) to record the
        stop in its status instead of treating it as a silent EOF."""
        return self.stop_controller.stop_reason

    def on_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or task.exception() is None:
            return
        logger.error(
            "pipeline task %r crashed", task.get_name(), exc_info=task.exception()
        )
        # Record the crash so the manager can tell an unexpected failure
        # apart from a natural end-of-source (which never sets this).
        if self.failure_exception is None:
            self.failure_exception = task.exception()
        # Close the broadcaster so clients end cleanly instead of stalling
        # on a pipeline that silently stopped producing.
        if self.failure_close_task is None:
            self.failure_close_task = asyncio.create_task(
                self.broadcaster.close()
            )

    async def forward_frames(self) -> None:
        frame_count = 0
        async for frame in self.source.frames():
            frame_count += 1
            self.stop_controller.observe(FrameRead(frame_count))
            if self.stop_controller.triggered:
                break  # this frame counts as read, not processed
            rendered_frame = await self.engine.process(frame)
            # process() fires TracksUpdated events via the Engine's
            # on_tracks hook, so a track-based condition may have
            # triggered just now.
            if self.stop_controller.triggered:
                break
            if rendered_frame is None:
                # Engine is still filling its lookahead delay buffer.
                continue
            await self.encoder.write_frame(rendered_frame)
        # Source exhausted (or a stop condition triggered): EOF the
        # encoder's input so it flushes its trailing fragment(s) and EOFs
        # its output, which lets read_encoder_output finish and close the
        # broadcaster — a condition stop drains exactly like natural EOF.
        await self.encoder.close_input()

    async def read_encoder_output(self) -> None:
        while True:
            output_unit = await self.encoder.read_output()
            if output_unit is None:
                # End of stream: wake every client so they finish instead
                # of polling a stream that will never produce again.
                await self.broadcaster.close()
                break
            if output_unit.kind == "init":
                await self.broadcaster.set_init_segment(output_unit.payload)
            else:
                await self.broadcaster.publish_fragment(output_unit.payload)


# ──────────────────────────────────────────────────────────────────────────
# PipelineManager — cf. app's pipeline.py (minus the web app)
# ──────────────────────────────────────────────────────────────────────────


class SourceError(TypedDict):
    """Unexpected-failure record surfaced to clients. The monotonic id lets
    a consumer show each failure exactly once."""

    id: int
    source: str | None
    message: str


class StopRecord(TypedDict):
    """Condition-triggered-stop record surfaced to clients: why a stop
    condition ended the stream. Deliberate, so distinct from SourceError;
    the monotonic id lets a consumer show each stop exactly once."""

    id: int
    source: str | None
    reason: str


class PipelineStatus(TypedDict):
    state: Literal["idle", "playing"]
    stream_id: str | None
    source: str | None
    error: SourceError | None
    stopped: StopRecord | None


@dataclass(frozen=True)
class PipelineComponents[FrameT, DetectionT: Interpolatable, OutT, PayloadT]:
    """Per-build factories for every pluggable seam. Factories (rather than
    instances) because each build must get a fresh source/encoder — a
    genuinely new session, like the real app's fresh ffmpeg pair per build.
    Stop conditions are factories for the same reason: they may be stateful
    (frame counters, deadlines) and each build needs fresh instances."""

    source_factory: Callable[[], AbstractAsyncContextManager[FrameSource[FrameT]]]
    detector_factory: Callable[[], Detector[FrameT, DetectionT]]
    embedder_factory: Callable[[], Embedder[FrameT, DetectionT]]
    tracker_factory: Callable[[float], Tracker[DetectionT]]  # takes the source fps
    overlay: Overlay[FrameT, DetectionT, OutT]
    encoder_factory: Callable[
        [float], AbstractAsyncContextManager[Encoder[OutT, PayloadT]]
    ]
    config: EngineConfig = EngineConfig()
    max_fragments: int = 15
    # Any-of: the first condition to fire ends the stream gracefully.
    stop_condition_factories: Sequence[Callable[[], StopCondition[DetectionT]]] = ()


class PipelineManager[FrameT, DetectionT: Interpolatable, OutT, PayloadT]:
    """Owns the shared pipeline's lifecycle: it starts idle (no source),
    builds a pipeline on demand, and tears back down to idle when the source
    ends, fails, or is explicitly stopped.

    The whole stack lives in an AsyncExitStack so it can be torn down and
    rebuilt at runtime, not just via a fixed `async with` block scope. The
    pipeline is a singleton per manager: all clients share one broadcaster
    and differ only in which fragments they've consumed.
    """

    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.lock = asyncio.Lock()
        self.generation = 0
        self.closing = False
        self.watchdog_tasks: set[asyncio.Task[None]] = set()

        # The currently-running pipeline's orchestrator (for its crash
        # reason) and a human label for the active source; None while idle.
        self.orchestrator: (
            Orchestrator[FrameT, DetectionT, OutT, PayloadT] | None
        ) = None
        self.current_source_label: str | None = None
        self.last_error: SourceError | None = None
        self.error_sequence_number = 0
        self.last_stop: StopRecord | None = None
        self.stop_sequence_number = 0

        # What app.state held in the real app; None while idle, so anything
        # routing on the manager must tolerate that.
        self.broadcaster: Broadcaster[PayloadT] | None = None
        self.engine: Engine[FrameT, DetectionT, OutT] | None = None
        self.stream_id: str | None = None

    async def start(
        self,
        components: PipelineComponents[FrameT, DetectionT, OutT, PayloadT],
        source_label: str,
    ) -> None:
        async with self.lock:
            await self.build(components, source_label)

    async def go_idle(self) -> None:
        """Explicit stop: tear the pipeline down to idle. Not a failure, so
        no error is recorded."""
        async with self.lock:
            await self.go_idle_locked(error=None)

    async def build(
        self,
        components: PipelineComponents[FrameT, DetectionT, OutT, PayloadT],
        source_label: str,
    ) -> None:
        # Teardown-first: only one pipeline ever runs at a time, at the cost
        # of a client-visible gap during the rebuild (clients see the closed
        # broadcaster and poll until the new pipeline is up). A watchdog
        # spawned below takes the pipeline back to idle if it later closes
        # on its own (source exhaustion, a crash, ...). If this build itself
        # raises, self.generation is never bumped, so the watchdog watching
        # the *old* (just-closed-by-teardown-above) broadcaster still
        # matches the current generation once it wakes — it takes the
        # pipeline to idle instead of leaving a half-dead stack.
        await self.teardown_locked()

        new_stack = AsyncExitStack()
        try:
            source = await new_stack.enter_async_context(
                components.source_factory()
            )
            fps = source.fps
            encoder = await new_stack.enter_async_context(
                components.encoder_factory(fps)
            )
            # Fresh stop-condition instances per build (they're stateful);
            # the controller latches the first reason any of them returns.
            stop_controller: StopController[DetectionT] = StopController(
                [factory() for factory in components.stop_condition_factories]
            )
            engine = await new_stack.enter_async_context(
                running(
                    Engine(
                        detector=components.detector_factory(),
                        embedder=components.embedder_factory(),
                        tracker=components.tracker_factory(fps),
                        overlay=components.overlay,
                        fps=fps,
                        config=components.config,
                        on_tracks=lambda frame_index, tracks: (
                            stop_controller.observe(
                                TracksUpdated(frame_index, tracks)
                            )
                        ),
                    )
                )
            )
            broadcaster = await new_stack.enter_async_context(
                running(Broadcaster(max_fragments=components.max_fragments))
            )
            orchestrator = await new_stack.enter_async_context(
                running(
                    Orchestrator(
                        source, engine, encoder, broadcaster, stop_controller
                    ).start()
                )
            )
        except BaseException:
            await new_stack.aclose()
            raise

        self.stack = new_stack
        self.orchestrator = orchestrator
        self.current_source_label = source_label
        self.last_error = None
        self.last_stop = None
        self.broadcaster = broadcaster
        self.engine = engine
        self.stream_id = str(uuid.uuid4())
        self.generation += 1
        generation = self.generation
        task = asyncio.create_task(
            self.watch_and_idle(broadcaster, generation),
            name=f"pipeline-watchdog-{generation}",
        )
        self.watchdog_tasks.add(task)
        task.add_done_callback(self.watchdog_tasks.discard)

    async def watch_and_idle(
        self, broadcaster: Broadcaster[PayloadT], generation: int
    ) -> None:
        await broadcaster.wait_closed()
        async with self.lock:
            # A newer generation already replaced this pipeline (an explicit
            # stop, or a source switch) — nothing to do; that action owns
            # the transition.
            if self.closing or generation != self.generation:
                return
            # The pipeline closed on its own. A crash carries a failure
            # reason (-> error record, crashes win); a triggered stop
            # condition carries a stop reason (-> stop record); a natural
            # end-of-source carries neither (-> silent idle).
            reason = (
                self.orchestrator.failure_reason if self.orchestrator else None
            )
            stop_reason = (
                self.orchestrator.stop_reason if self.orchestrator else None
            )
            source = self.current_source_label
            error: SourceError | None = None
            stop: StopRecord | None = None
            if reason is not None:
                self.error_sequence_number += 1
                error = SourceError(
                    id=self.error_sequence_number, source=source, message=reason
                )
                logger.warning(
                    "pipeline (generation %s) failed on source %r: %s",
                    generation,
                    source,
                    reason,
                )
            elif stop_reason is not None:
                self.stop_sequence_number += 1
                stop = StopRecord(
                    id=self.stop_sequence_number,
                    source=source,
                    reason=stop_reason,
                )
                logger.info(
                    "pipeline (generation %s) stopped by condition on "
                    "source %r: %s",
                    generation,
                    source,
                    stop_reason,
                )
            else:
                logger.info(
                    "pipeline (generation %s) reached end of source %r; "
                    "going idle",
                    generation,
                    source,
                )
            await self.go_idle_locked(error=error, stop=stop)

    async def go_idle_locked(
        self, error: SourceError | None, stop: StopRecord | None = None
    ) -> None:
        # Bump the generation first so any other watchdog waiting on the
        # lock sees a mismatch and returns instead of double-handling.
        self.generation += 1
        await self.teardown_locked()
        if error is not None:
            self.last_error = error
        if stop is not None:
            self.last_stop = stop

    async def teardown_locked(self) -> None:
        """Close the current stack (if any) and reset all per-pipeline
        state. Callers must hold self.lock. Leaves self.last_error and
        self.last_stop alone: an explicit stop or a fresh build decides
        what happens to them."""
        await self.stack.aclose()
        self.stack = AsyncExitStack()
        self.orchestrator = None
        self.current_source_label = None
        self.broadcaster = None
        self.engine = None
        self.stream_id = None

    def status(self) -> PipelineStatus:
        return PipelineStatus(
            state="playing" if self.stream_id is not None else "idle",
            stream_id=self.stream_id,
            source=self.current_source_label,
            error=self.last_error,
            stopped=self.last_stop,
        )

    async def aclose(self) -> None:
        self.closing = True
        tasks = list(self.watchdog_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.stack.aclose()


# ──────────────────────────────────────────────────────────────────────────
# GreedyIoUTracker — an in-file stand-in for the ByteTrack wrapper
# ──────────────────────────────────────────────────────────────────────────


def expand(
    bbox: tuple[float, float, float, float], expand_ratio: float
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    pad_x = (x2 - x1) * expand_ratio / 2.0
    pad_y = (y2 - y1) * expand_ratio / 2.0
    return (x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y)


def iou(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> float:
    intersect_x1 = max(box_a[0], box_b[0])
    intersect_y1 = max(box_a[1], box_b[1])
    intersect_x2 = min(box_a[2], box_b[2])
    intersect_y2 = min(box_a[3], box_b[3])
    intersection_area = max(0.0, intersect_x2 - intersect_x1) * max(
        0.0, intersect_y2 - intersect_y1
    )
    if intersection_area <= 0.0:
        return 0.0
    union_area = (
        (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
        + (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
        - intersection_area
    )
    return intersection_area / union_area if union_area > 0.0 else 0.0


@dataclass
class TrackState:
    bbox: tuple[float, float, float, float]
    misses: int = 0


class GreedyIoUTracker[D: HasBBox]:
    """Minimal multi-object tracker behind the Tracker protocol.

    A deliberately small stand-in for the real ByteTrack wrapper (which is a
    third-party dependency): greedy IoU association instead of Kalman +
    Hungarian, same contract — stable ids, matched *input* detections, and
    per-track persisted embeddings. A real deployment plugs ByteTrack in
    behind the same protocol.

    The tracker is updated at detection cadence (a few Hz), not video frame
    rate, so a fast object can move nearly its own width between updates —
    plain IoU association sees zero overlap and churns track ids. Buffered
    IoU expands boxes before matching to bridge that gap (the BIoU of the
    real wrapper).
    """

    def __init__(
        self,
        fps: float,
        *,
        lost_track_buffer: int = 30,
        track_activation_threshold: float = 0.25,
        buffer_ratio: float = 0.5,
        iou_threshold: float = 0.1,
    ) -> None:
        del fps  # signature parity with the real wrapper; unused here
        self.lost_track_buffer = lost_track_buffer
        self.activation_threshold = track_activation_threshold
        self.buffer_ratio = buffer_ratio
        self.iou_threshold = iou_threshold
        self.tracks: dict[int, TrackState] = {}
        self.embeddings: dict[int, NDArray[np.float32]] = {}
        self.next_id = 1

    def update(
        self, detections: list[D], embeddings: list[NDArray[np.float32]]
    ) -> list[Tracked[D]]:
        if not detections:
            return []

        # Score every (track, detection) pair by buffered IoU, then match
        # greedily in descending score order.
        candidate_matches: list[tuple[float, int, int]] = []
        for track_id, state in self.tracks.items():
            expanded_track_box = expand(state.bbox, self.buffer_ratio)
            for detection_index, detection in enumerate(detections):
                iou_score = iou(
                    expanded_track_box, expand(detection.bbox, self.buffer_ratio)
                )
                if iou_score >= self.iou_threshold:
                    candidate_matches.append(
                        (iou_score, track_id, detection_index)
                    )
        candidate_matches.sort(key=lambda match: match[0], reverse=True)

        track_id_by_detection: dict[int, int] = {}  # detection index -> track id
        matched_tracks: set[int] = set()
        for iou_score, track_id, detection_index in candidate_matches:
            if (
                track_id in matched_tracks
                or detection_index in track_id_by_detection
            ):
                continue
            matched_tracks.add(track_id)
            track_id_by_detection[detection_index] = track_id

        # Unmatched, confident detections start new tracks.
        for detection_index, detection in enumerate(detections):
            if detection_index in track_id_by_detection:
                continue
            if detection.confidence >= self.activation_threshold:
                track_id = self.next_id
                self.next_id += 1
                track_id_by_detection[detection_index] = track_id
                matched_tracks.add(track_id)

        # Unmatched tracks age out after lost_track_buffer missed updates.
        for track_id in list(self.tracks):
            if track_id in matched_tracks:
                continue
            state = self.tracks[track_id]
            state.misses += 1
            if state.misses > self.lost_track_buffer:
                del self.tracks[track_id]
                self.embeddings.pop(track_id, None)

        tracked_results: list[Tracked[D]] = []
        for detection_index in sorted(track_id_by_detection):
            track_id = track_id_by_detection[detection_index]
            detection = detections[detection_index]
            # Refresh the track's box and reset its miss counter.
            self.tracks[track_id] = TrackState(detection.bbox)
            if detection_index < len(embeddings):
                self.embeddings[track_id] = embeddings[detection_index]
            # The matched input detection, never an internal estimate.
            tracked_results.append(
                Tracked(
                    track_id=track_id,
                    detection=detection,
                    embedding=self.embeddings.get(track_id),
                )
            )
        return tracked_results


# ──────────────────────────────────────────────────────────────────────────
# Demo world: a synthetic FrameT with known ground truth
# ──────────────────────────────────────────────────────────────────────────


def truth_positions(frame_index: int) -> dict[str, tuple[float, float]]:
    """Ground-truth object centers at a frame index. Pure function, shared
    by the source (to build frames) and the final checks (to score accuracy).
    A stays in [80, 320]^2 and B in [500, 620]x[380, 460], so the two are
    always >= 180 px apart and nearest-truth matching is unambiguous."""
    time_index = float(frame_index)
    return {
        "A": (
            200.0 + 120.0 * math.cos(0.03 * time_index),
            200.0 + 120.0 * math.sin(0.03 * time_index),
        ),
        "B": (
            560.0 + 60.0 * math.sin(0.025 * time_index),
            420.0 + 40.0 * math.cos(0.018 * time_index),
        ),
    }


@dataclass(frozen=True)
class ObjectTruth:
    label: str
    center: tuple[float, float]


@dataclass(frozen=True)
class SimFrame:
    """The demo's FrameT: no pixels, just an index and the ground truth the
    fake detector will (noisily) observe. Frozen, so any Overlay that tried
    to mutate it would raise — the no-mutation contract is machine-checked."""

    index: int
    timestamp_seconds: float
    truth: tuple[ObjectTruth, ...]


@dataclass(frozen=True)
class BoxDetection:
    """The demo's DetectionT: satisfies both Interpolatable (for the lookahead
    buffer) and HasBBox (for the default tracker)."""

    bbox: tuple[float, float, float, float]
    confidence: float

    def to_vector(self) -> NDArray[np.float64]:
        return np.array(self.bbox, dtype=np.float64)

    def with_vector(self, vector: NDArray[np.float64]) -> BoxDetection:
        x1, y1, x2, y2 = (float(v) for v in vector)
        # Template semantics: confidence carries over from self.
        return BoxDetection(bbox=(x1, y1, x2, y2), confidence=self.confidence)


class SimSource:
    """FrameSource impl: paces frames out in real time at `fps`.

    Frame indices are 1-based to line up with the Engine's internal frame
    counter (incremented before buffering), so the detector's sampled-index
    ledger and the emitted render_frame_index values live on the same axis.
    """

    def __init__(self, fps: float = 100.0, n_frames: int = 600) -> None:
        self.fps_value = fps
        self.n_frames = n_frames
        self.frames_produced = 0

    @property
    def fps(self) -> float:
        return self.fps_value

    async def frames(self) -> AsyncIterator[SimFrame]:
        for frame_index in range(1, self.n_frames + 1):
            self.frames_produced = frame_index
            truth = tuple(
                ObjectTruth(label, center)
                for label, center in truth_positions(frame_index).items()
            )
            yield SimFrame(
                index=frame_index,
                timestamp_seconds=frame_index / self.fps,
                truth=truth,
            )
            await asyncio.sleep(1.0 / self.fps)


class NoisyDetector:
    """Detector impl: observes the frame's ground truth with Gaussian noise
    and a *blocking* latency proportional to batch size — proving that the
    Engine really does keep inference off the event loop (a blocking sleep
    on the loop would stall the 10 ms frame pacing visibly)."""

    def __init__(
        self,
        *,
        seed: int = 42,
        noise_px: float = 0.5,
        box_half_size: float = 15.0,
        base_latency_s: float = 0.040,
        per_frame_latency_s: float = 0.010,
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.noise_px = noise_px
        self.box_half_size = box_half_size
        self.base_latency_s = base_latency_s
        self.per_frame_latency_s = per_frame_latency_s
        # Every frame index this detector ever saw: the demo's snapshot
        # ledger, used by the no-extrapolation check.
        self.sampled_indices: list[int] = []

    def detect_batch(self, frames: Sequence[SimFrame]) -> list[list[BoxDetection]]:
        time.sleep(self.base_latency_s + self.per_frame_latency_s * len(frames))
        detections_per_frame: list[list[BoxDetection]] = []
        for frame in frames:
            self.sampled_indices.append(frame.index)
            detections: list[BoxDetection] = []
            for object_truth in frame.truth:
                center_x = object_truth.center[0] + float(
                    self.rng.normal(0.0, self.noise_px)
                )
                center_y = object_truth.center[1] + float(
                    self.rng.normal(0.0, self.noise_px)
                )
                half_size = self.box_half_size
                detections.append(
                    BoxDetection(
                        bbox=(
                            center_x - half_size,
                            center_y - half_size,
                            center_x + half_size,
                            center_y + half_size,
                        ),
                        confidence=0.9,
                    )
                )
            detections_per_frame.append(detections)
        return detections_per_frame


class ToyEmbedder:
    """Embedder impl: deterministic one-hot-ish unit vectors keyed by coarse
    position. Chunks at max_batch like the real ArcFace wrapper and records
    the largest chunk it ever processed so the demo can verify the chunking
    contract was honoured."""

    def __init__(self) -> None:
        self.max_chunk_seen = 0

    def embed_many(
        self, items: Sequence[tuple[SimFrame, BoxDetection]], max_batch: int
    ) -> list[NDArray[np.float32]]:
        embeddings: list[NDArray[np.float32]] = []
        for chunk_start in range(0, len(items), max_batch):
            chunk = items[chunk_start : chunk_start + max_batch]
            self.max_chunk_seen = max(self.max_chunk_seen, len(chunk))
            for _frame, detection in chunk:
                center_x = (detection.bbox[0] + detection.bbox[2]) / 2.0
                embedding_vector = np.zeros(8, dtype=np.float32)
                embedding_vector[int(center_x) // 160 % 8] = 1.0
                embeddings.append(embedding_vector)
        return embeddings


@dataclass(frozen=True)
class RenderedFrame:
    """The demo's OutT: what the Overlay 'draws' — pure data instead of
    pixels, so the final checks can inspect exactly what would be on screen."""

    render_frame_index: int
    caption: str
    live_index_at_render: int
    boxes: tuple[tuple[int, tuple[float, float, float, float]], ...]
    is_interpolated: bool


class SimOverlay:
    """Overlay impl: builds a fresh RenderedFrame (never touches SimFrame —
    it couldn't anyway, SimFrame is frozen). Reads the source's live frame
    counter so every rendered frame records how far it trails the live edge."""

    def __init__(self, source: SimSource) -> None:
        self.source = source

    def render(
        self,
        frame: SimFrame,
        caption: str,
        tracked: Sequence[Tracked[BoxDetection]],
        is_interpolated: bool,
    ) -> RenderedFrame:
        return RenderedFrame(
            render_frame_index=frame.index,
            caption=caption,
            live_index_at_render=self.source.frames_produced,
            boxes=tuple((t.track_id, t.detection.bbox) for t in tracked),
            is_interpolated=is_interpolated,
        )


@dataclass(frozen=True)
class DemoInit:
    """The demo's init segment (ftyp..moov analog)."""

    fps: float
    gop: int


@dataclass(frozen=True)
class DemoFragment:
    """The demo's media fragment (moof+mdat analog): a GOP of frames."""

    frames: tuple[RenderedFrame, ...]


type DemoPayload = DemoInit | DemoFragment


class ChunkingEncoder:
    """Encoder impl: groups rendered frames into fixed-size fragments, with
    an init unit first. close_input flushes the partial trailing fragment
    and then EOFs — mirroring ffmpeg's flush-on-stdin-EOF contract."""

    def __init__(self, fps: float, gop: int = 10) -> None:
        self.fps = fps
        self.gop = gop
        self.buffer: list[RenderedFrame] = []
        self.sent_init = False
        self.queue: asyncio.Queue[EncoderOutput[DemoPayload] | None] = (
            asyncio.Queue()
        )

    async def write_frame(self, frame: RenderedFrame) -> None:
        if not self.sent_init:
            self.sent_init = True
            await self.queue.put(
                EncoderOutput("init", DemoInit(fps=self.fps, gop=self.gop))
            )
        self.buffer.append(frame)
        if len(self.buffer) >= self.gop:
            await self._flush()

    async def _flush(self) -> None:
        if self.buffer:
            await self.queue.put(
                EncoderOutput("fragment", DemoFragment(tuple(self.buffer)))
            )
            self.buffer = []

    async def close_input(self) -> None:
        await self._flush()
        await self.queue.put(None)

    async def read_output(self) -> EncoderOutput[DemoPayload] | None:
        return await self.queue.get()


# ──────────────────────────────────────────────────────────────────────────
# Demo clients (cf. the player's stream-handler loop)
# ──────────────────────────────────────────────────────────────────────────


async def follow(
    broadcaster: Broadcaster[DemoPayload], last_seen_sequence: int
) -> AsyncIterator[Fragment[DemoPayload]]:
    """Live-tail: yield each new fragment until the broadcaster closes.

    LaggedError from wait_for_next propagates to the caller — falling off
    the retention window ends the connection, it isn't recoverable here.
    """
    while True:
        fragment = await broadcaster.wait_for_next(
            last_seen_sequence, timeout=0.5
        )
        if fragment is None:
            if broadcaster.is_closed:
                return
            continue
        yield fragment
        last_seen_sequence = fragment.sequence_number


async def run_fast_client(
    broadcaster: Broadcaster[DemoPayload], frames_out: list[RenderedFrame]
) -> str:
    """A client that keeps up: init + latest fragment, then live-tail."""
    snapshot = broadcaster.snapshot_for_new_client()
    last_seen_sequence = -1
    if snapshot.fragment is not None:
        payload = snapshot.fragment.payload
        if isinstance(payload, DemoFragment):
            frames_out.extend(payload.frames)
        last_seen_sequence = snapshot.fragment.sequence_number
    try:
        async for fragment in follow(broadcaster, last_seen_sequence):
            if isinstance(fragment.payload, DemoFragment):
                frames_out.extend(fragment.payload.frames)
    except LaggedError:
        return "lagged"
    return "closed"


async def run_slow_client(broadcaster: Broadcaster[DemoPayload]) -> str:
    """A client that naps long enough to fall off the retention deque: with
    fragments every ~100 ms and max_fragments=4 (~0.4 s of retention), a
    1.2 s nap leaves it ~8 fragments behind — LaggedError guaranteed.

    Joining goes through snapshot_for_new_client, like the real player: a
    new client starts from the *latest* fragment, so it can never be lagged
    at connect time — only by falling behind afterwards."""
    await asyncio.sleep(1.0)
    snapshot = broadcaster.snapshot_for_new_client()
    if snapshot.fragment is None:
        return "no-data"
    await asyncio.sleep(1.2)
    try:
        async for _fragment in follow(
            broadcaster, snapshot.fragment.sequence_number
        ):
            pass
    except LaggedError:
        return "lagged"
    return "closed"


async def print_metrics(
    engine: Engine[SimFrame, BoxDetection, RenderedFrame],
    broadcaster: Broadcaster[DemoPayload],
) -> None:
    while not broadcaster.is_closed:
        logger.info("metrics: %s", engine.metrics_snapshot())
        await asyncio.sleep(1.0)


# ──────────────────────────────────────────────────────────────────────────
# main(): run the whole stack on synthetic data, then check the invariants
# ──────────────────────────────────────────────────────────────────────────

_failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {message}")
    if not condition:
        _failures.append(message)


def run_checks(
    received: list[RenderedFrame],
    detector: NoisyDetector,
    embedder: ToyEmbedder,
    fast_result: str,
    slow_result: str,
    status: PipelineStatus,
    manager: PipelineManager[SimFrame, BoxDetection, RenderedFrame, DemoPayload],
) -> None:
    check(len(received) >= 300, f"received a healthy frame count ({len(received)})")

    # (a) Contiguity + delay: the render cursor advances 0 or 1 frame per
    # emission (0 while capped waiting for lookahead), covers a contiguous
    # index range, and always trails the live frame.
    render_frame_indices = [frame.render_frame_index for frame in received]
    cursor_deltas = {
        later - earlier
        for earlier, later in zip(
            render_frame_indices, render_frame_indices[1:], strict=False
        )
    }
    check(
        cursor_deltas <= {0, 1},
        f"(a) render-cursor deltas are all in {{0, 1}} (saw {cursor_deltas})",
    )
    distinct_indices = sorted(set(render_frame_indices))
    check(
        distinct_indices
        == list(range(distinct_indices[0], distinct_indices[-1] + 1)),
        "(a) emitted render_frame_index range is contiguous "
        f"({distinct_indices[0]}..{distinct_indices[-1]})",
    )
    check(
        all(
            frame.live_index_at_render - frame.render_frame_index >= 1
            for frame in received
        ),
        "(a) every emitted frame trails the live frame",
    )

    # (b) Accuracy: interpolated box centers track the ground-truth
    # trajectories. Also, every frame carries exactly 2 boxes — the tripwire
    # for the keep-render_frame_index rule (a wrong off-by-one silently
    # routes emission through the box-less fallback path).
    check(
        all(len(frame.boxes) == 2 for frame in received),
        "(b) every emitted frame has exactly 2 boxes",
    )
    center_errors: list[float] = []
    for frame in received:
        truth_centers = truth_positions(frame.render_frame_index)
        for _track_id, bbox in frame.boxes:
            center_x = (bbox[0] + bbox[2]) / 2.0
            center_y = (bbox[1] + bbox[3]) / 2.0
            center_errors.append(
                min(
                    math.hypot(center_x - truth_x, center_y - truth_y)
                    for truth_x, truth_y in truth_centers.values()
                )
            )
    mean_error = (
        sum(center_errors) / len(center_errors) if center_errors else float("inf")
    )
    max_error = max(center_errors) if center_errors else float("inf")
    check(mean_error <= 1.5, f"(b) mean center error {mean_error:.2f} px <= 1.5 px")
    check(max_error <= 4.0, f"(b) max center error {max_error:.2f} px <= 4.0 px")

    # (c) No extrapolation: solid frames land exactly on a detection
    # snapshot; interpolated frames lie strictly between two consecutive
    # snapshots.
    sampled_indices = sorted(set(detector.sampled_indices))
    sampled_index_set = set(sampled_indices)

    def within_segment(render_frame_index: int) -> bool:
        insertion_point = bisect_left(sampled_indices, render_frame_index)
        return (
            0 < insertion_point < len(sampled_indices)
            and sampled_indices[insertion_point - 1]
            < render_frame_index
            < sampled_indices[insertion_point]
        )

    check(
        all(
            frame.render_frame_index in sampled_index_set
            for frame in received
            if not frame.is_interpolated
        ),
        "(c) every solid frame lands on a real detection snapshot",
    )
    check(
        all(
            within_segment(frame.render_frame_index)
            for frame in received
            if frame.is_interpolated
        ),
        "(c) every interpolated frame lies between two real detections",
    )

    # (d) Track-id stability: two objects, two ids, and each ground-truth
    # object maps to exactly one id for the whole run.
    track_ids_by_label: dict[str, set[int]] = {}
    for frame in received:
        truth_centers = truth_positions(frame.render_frame_index)
        for track_id, bbox in frame.boxes:
            center_x = (bbox[0] + bbox[2]) / 2.0
            center_y = (bbox[1] + bbox[3]) / 2.0
            label = min(
                truth_centers,
                key=lambda candidate_label: math.hypot(
                    center_x - truth_centers[candidate_label][0],
                    center_y - truth_centers[candidate_label][1],
                ),
            )
            track_ids_by_label.setdefault(label, set()).add(track_id)
    all_ids = set().union(*track_ids_by_label.values()) if track_ids_by_label else set()
    check(len(all_ids) == 2, f"(d) exactly 2 track ids over the run ({all_ids})")
    check(
        all(len(ids) == 1 for ids in track_ids_by_label.values()),
        f"(d) each object kept one stable id ({track_ids_by_label})",
    )

    # (e) The lagging client was told it can't catch up.
    check(slow_result == "lagged", f"(e) slow client lagged (got {slow_result!r})")
    check(fast_result == "closed", f"(e) fast client ended cleanly ({fast_result!r})")

    # (f) Clean EOF took the manager to idle, silently.
    check(status["state"] == "idle", "(f) manager is idle after end of source")
    check(status["error"] is None, "(f) clean EOF recorded no error")
    check(status["stopped"] is None, "(f) clean EOF recorded no stop")
    check(manager.broadcaster is None, "(f) idle state cleared the broadcaster")

    # Bonus: the embedder never saw a chunk larger than its max_batch.
    check(
        0 < embedder.max_chunk_seen <= 3,
        f"embedder chunking honoured (max chunk {embedder.max_chunk_seen} <= 3)",
    )


def run_stop_checks(
    received: list[RenderedFrame],
    source: SimSource,
    client_result: str,
    status: PipelineStatus,
) -> None:
    # (g) Stop conditions: the second run stops early via MaxFramesRead
    # while its never-firing siblings (any-of composition) stay quiet, the
    # stream drains gracefully, and the stop is recorded — not as an error.
    check(
        source.frames_produced == 150,
        "(g) stop condition halted the source at exactly 150 frames read "
        f"({source.frames_produced})",
    )
    check(status["state"] == "idle", "(g) manager is idle after condition stop")
    check(status["error"] is None, "(g) condition stop recorded no error")
    stopped = status["stopped"]
    check(
        stopped is not None and "limit 150" in stopped["reason"],
        f"(g) stop record names the frame limit ({stopped})",
    )
    check(
        stopped is not None and stopped["id"] == 1,
        "(g) stop record carries the first monotonic id",
    )
    check(
        client_result == "closed",
        f"(g) client ended cleanly after condition stop ({client_result!r})",
    )
    check(
        len(received) > 0
        and all(frame.render_frame_index < 150 for frame in received),
        f"(g) graceful drain delivered {len(received)} frames, all from "
        "before the stop point",
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )

    source = SimSource(fps=100.0, n_frames=600)
    detector = NoisyDetector()
    embedder = ToyEmbedder()

    components = PipelineComponents[
        SimFrame, BoxDetection, RenderedFrame, DemoPayload
    ](
        source_factory=lambda: nullcontext(source),
        detector_factory=lambda: detector,
        embedder_factory=lambda: embedder,
        tracker_factory=lambda fps: GreedyIoUTracker(fps),
        overlay=SimOverlay(source),
        encoder_factory=lambda fps: nullcontext(ChunkingEncoder(fps, gop=10)),
        # embed_max_batch=3: 2 objects x 4 sampled frames = 8 crops per pass
        # -> 3 chunks, so the chunking path is exercised on every batch.
        config=EngineConfig(
            batch_frames=4, embed_max_batch=3, max_batch_lag_ms=25.0, lookahead=2
        ),
        max_fragments=4,
    )

    manager: PipelineManager[
        SimFrame, BoxDetection, RenderedFrame, DemoPayload
    ] = PipelineManager()
    await manager.start(components, "synthetic demo")
    logger.info("pipeline playing: %s", manager.status())

    broadcaster = manager.broadcaster
    engine = manager.engine
    if broadcaster is None or engine is None:  # pragma: no cover - just started
        raise RuntimeError("pipeline failed to expose broadcaster/engine")

    received: list[RenderedFrame] = []
    fast_task = asyncio.create_task(run_fast_client(broadcaster, received))
    slow_task = asyncio.create_task(run_slow_client(broadcaster))
    metrics_task = asyncio.create_task(print_metrics(engine, broadcaster))

    # The source is finite: run to natural EOF, which closes the broadcaster
    # and (via the watchdog) takes the manager back to idle.
    await broadcaster.wait_closed()
    fast_result = await fast_task
    slow_result = await slow_task
    await metrics_task

    deadline = time.monotonic() + 2.0
    while manager.status()["state"] != "idle" and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    status = manager.status()

    # Second run, on the same manager (exercising rebuild-after-idle): a
    # list of stop conditions where only MaxFramesRead can fire — the demo
    # world always has exactly 2 tracks and finishes far under 30 s — so
    # the any-of composition must stop the stream at frame 150, long
    # before the source's natural end at frame 600.
    stop_source = SimSource(fps=100.0, n_frames=600)
    stop_components = PipelineComponents[
        SimFrame, BoxDetection, RenderedFrame, DemoPayload
    ](
        source_factory=lambda: nullcontext(stop_source),
        detector_factory=lambda: NoisyDetector(),
        embedder_factory=lambda: ToyEmbedder(),
        tracker_factory=lambda fps: GreedyIoUTracker(fps),
        overlay=SimOverlay(stop_source),
        encoder_factory=lambda fps: nullcontext(ChunkingEncoder(fps, gop=10)),
        config=EngineConfig(
            batch_frames=4, embed_max_batch=3, max_batch_lag_ms=25.0, lookahead=2
        ),
        max_fragments=4,
        stop_condition_factories=(
            lambda: TracksMatch(
                lambda tracks: len(tracks) >= 3, ">= 3 concurrent tracks"
            ),
            lambda: MaxFramesRead(150),
            lambda: MaxDuration(30.0),
        ),
    )
    await manager.start(stop_components, "stop-condition demo")
    logger.info("pipeline playing: %s", manager.status())

    stop_broadcaster = manager.broadcaster
    if stop_broadcaster is None:  # pragma: no cover - just started
        raise RuntimeError("pipeline failed to expose broadcaster")
    stop_received: list[RenderedFrame] = []
    stop_client_task = asyncio.create_task(
        run_fast_client(stop_broadcaster, stop_received)
    )
    await stop_broadcaster.wait_closed()
    stop_client_result = await stop_client_task

    deadline = time.monotonic() + 2.0
    while manager.status()["state"] != "idle" and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    stop_status = manager.status()
    await manager.aclose()

    print()
    run_checks(
        received, detector, embedder, fast_result, slow_result, status, manager
    )
    run_stop_checks(stop_received, stop_source, stop_client_result, stop_status)
    if _failures:
        raise SystemExit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    asyncio.run(main())

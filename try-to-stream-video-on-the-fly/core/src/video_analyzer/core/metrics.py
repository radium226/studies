"""Live pipeline metrics as trailing time-window moving averages, plus the
three service decorators that feed them.

Ported from `app/metrics.py`, where every number was recorded from one
monolithic `Engine.process`. Here the same numbers come from three different
`kernel.Pipeline` stages, none of which the composing application can reach
directly — so the collector is fed by decorators rather than called inline:
`MeteredFaceDetector` (stage 2) times detection, `MeteredFaceEmbedder`
(stage 3) times embedding, and `MeteredFrameBroadcaster` (stage 6) counts
rendered frames and live tracks. That is the same rule the stop strategies
follow: an observation concern becomes a decorator around an existing service,
never a new `kernel` service ABC.

Every `record_*` call happens on the event loop — the decorators time the
`await`, they do not run inside the detector's executor — and `snapshot()` is
read from a request handler on that same loop, so there is no cross-thread
access and no locking is needed. Each metric is a rolling window of recent
samples; averages and rates cover the last `config.window_s` seconds, so the
numbers track *current* behaviour rather than a lifetime mean.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from video_analyzer import kernel

from .config import MetricsCollectorConfig


class _Window:
    """Trailing time-window of `(timestamp, value)` samples."""

    def __init__(self, window_s: float, clock: kernel.Clock) -> None:
        self._window_s = window_s
        self._clock = clock
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, value: float) -> None:
        now = self._clock.now()
        self._samples.append((now, value))
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def mean(self) -> float:
        self._evict(self._clock.now())
        if not self._samples:
            return 0.0
        return sum(value for _, value in self._samples) / len(self._samples)

    def rate(self) -> float:
        """Samples per second, averaged over the window (a per-second count)."""
        self._evict(self._clock.now())
        if not self._samples:
            return 0.0
        return len(self._samples) / self._window_s


class MetricsCollector:
    """Accumulates what the three `Metered*` decorators observe.

    The clock is a collaborator, so it stays a constructor argument; only
    `window_s` tunes behaviour, so only it lives in the config (same rule as
    everywhere else in `kernel`/`core`).
    """

    def __init__(
        self,
        clock: kernel.Clock,
        *,
        config: MetricsCollectorConfig | None = None,
    ) -> None:
        self.config = config if config is not None else MetricsCollectorConfig()
        window_s = self.config.window_s
        self._faces_per_frame = _Window(window_s, clock)
        self._detect_ms = _Window(window_s, clock)
        self._embed_ms = _Window(window_s, clock)
        self._detection_passes = _Window(window_s, clock)
        self._rendered_frames = _Window(window_s, clock)
        self._detected_frames = _Window(window_s, clock)
        self._detect_batch_size = _Window(window_s, clock)
        self._embed_batch_size = _Window(window_s, clock)
        self._active_tracks = 0

    def record_detection_pass(
        self,
        *,
        duration_s: float,
        frame_count: int,
        face_counts: Iterable[int],
    ) -> None:
        self._detect_ms.add(duration_s * 1000.0)
        self._detection_passes.add(1.0)
        self._detect_batch_size.add(float(frame_count))
        for count in face_counts:
            self._faces_per_frame.add(float(count))
            # One sample per frame actually run through detection, so its rate
            # is detected-frames/sec — the same units as processed fps, which
            # is what makes their ratio a stride.
            self._detected_frames.add(1.0)

    def record_embedding_pass(self, *, duration_s: float, crop_count: int) -> None:
        self._embed_ms.add(duration_s * 1000.0)
        self._embed_batch_size.add(float(crop_count))

    def record_rendered_frame(self, *, active_tracks: int) -> None:
        self._rendered_frames.add(1.0)
        self._active_tracks = active_tracks

    def snapshot(self) -> dict[str, float]:
        # Stride: rendered frames per frame actually detected — how many video
        # frames the interpolator covers for each real detection.
        detected_fps = self._detected_frames.rate()
        processed_fps = self._rendered_frames.rate()
        stride = processed_fps / detected_fps if detected_fps > 0 else 0.0
        detection_ms = self._detect_ms.mean()
        embedding_ms = self._embed_ms.mean()
        return {
            "detections_per_frame": round(self._faces_per_frame.mean(), 2),
            "detection_ms": round(detection_ms, 1),
            "embedding_ms": round(embedding_ms, 1),
            # `app/` sampled one combined duration per batch because one call
            # site did both. Detection and embedding are separate stages here,
            # but still strictly 1:1 (stage 3 embeds exactly the batch stage 2
            # produced), so summing the two means reconstructs the same number.
            "batch_ms": round(detection_ms + embedding_ms, 1),
            "detection_hz": round(self._detection_passes.rate(), 2),
            "detection_stride": round(stride, 1),
            "processed_fps": round(processed_fps, 1),
            "active_tracks": self._active_tracks,
            "detect_batch_size": round(self._detect_batch_size.mean(), 1),
            "embed_batch_size": round(self._embed_batch_size.mean(), 1),
            "window_s": self.config.window_s,
        }


class MeteredFaceDetector[FrameContentT](kernel.FaceDetector[FrameContentT]):
    """Times a `FaceDetector` pass and records its batch size and per-frame
    face counts. Timing wraps the `await`, so it measures the whole pass as the
    pipeline experiences it (executor queueing included), matching what
    `app/engine.py` measured around its own `run_in_executor` call."""

    def __init__(
        self,
        wrapped: kernel.FaceDetector[FrameContentT],
        metrics: MetricsCollector,
        clock: kernel.Clock,
    ) -> None:
        self._wrapped = wrapped
        self._metrics = metrics
        self._clock = clock

    async def detect_faces(
        self,
        frame_batch: list[kernel.Frame[FrameContentT]],
    ) -> list[list[kernel.Face[None]]]:
        started_at = self._clock.now()
        faces_per_frame = await self._wrapped.detect_faces(frame_batch)
        self._metrics.record_detection_pass(
            duration_s=self._clock.now() - started_at,
            frame_count=len(frame_batch),
            face_counts=[len(faces) for faces in faces_per_frame],
        )
        return faces_per_frame


class MeteredFaceEmbedder[FrameContentT, FaceEmbeddingT](
    kernel.FaceEmbedder[FrameContentT, FaceEmbeddingT]
):
    """Times a `FaceEmbedder` pass and records how many crops it covered.

    The crop count comes from the input pairs, not the result, so a pass that
    raises is simply not recorded at all rather than recorded as zero work."""

    def __init__(
        self,
        wrapped: kernel.FaceEmbedder[FrameContentT, FaceEmbeddingT],
        metrics: MetricsCollector,
        clock: kernel.Clock,
    ) -> None:
        self._wrapped = wrapped
        self._metrics = metrics
        self._clock = clock

    async def embed_faces(
        self,
        face_batch: list[tuple[kernel.Frame[FrameContentT], kernel.Face[None]]],
    ) -> list[kernel.Face[FaceEmbeddingT]]:
        started_at = self._clock.now()
        embedded = await self._wrapped.embed_faces(face_batch)
        self._metrics.record_embedding_pass(
            duration_s=self._clock.now() - started_at,
            crop_count=len(face_batch),
        )
        return embedded


class MeteredFrameBroadcaster[FrameContentT, FaceRecordT](
    kernel.FrameBroadcaster[FrameContentT, FaceRecordT]
):
    """Counts rendered frames (the pipeline's real output rate) and the number
    of faces on the most recent one.

    `active_tracks` is therefore a *rendered-frame* count, not the tracker's
    internal live-track set: a track the tracker still holds but that no longer
    appears on screen is not counted. `app/` read it straight off its tracker,
    which is the same number in practice and a more honest one here, since this
    is the only place a composing application can observe the pipeline without
    a new kernel contract."""

    def __init__(
        self,
        wrapped: kernel.FrameBroadcaster[FrameContentT, FaceRecordT],
        metrics: MetricsCollector,
    ) -> None:
        self._wrapped = wrapped
        self._metrics = metrics

    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[FrameContentT, FaceRecordT],
    ) -> None:
        await self._wrapped.broadcast_frame(annotated_frame)
        self._metrics.record_rendered_frame(active_tracks=len(annotated_frame.faces))

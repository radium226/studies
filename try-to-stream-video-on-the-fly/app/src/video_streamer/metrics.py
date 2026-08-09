"""Live pipeline metrics as trailing time-window moving averages.

Every value is recorded from `Engine.process` (i.e. on the event loop) and read
from the `/metrics` request handler (also on the event loop), so there is no
cross-thread access and no locking is needed. Each metric is a rolling window of
recent samples; averages and rates are computed over the last `window_s`
seconds, so the numbers track the *current* behaviour rather than a lifetime
mean.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable


class _Window:
    """Trailing time-window of `(timestamp, value)` samples."""

    def __init__(self, window_s: float, clock: Callable[[], float]) -> None:
        self._window_s = window_s
        self._clock = clock
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, value: float) -> None:
        now = self._clock()
        self._samples.append((now, value))
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def mean(self) -> float:
        self._evict(self._clock())
        if not self._samples:
            return 0.0
        return sum(v for _, v in self._samples) / len(self._samples)

    def rate(self) -> float:
        """Samples per second, averaged over the window (a per-second count)."""
        self._evict(self._clock())
        if not self._samples:
            return 0.0
        return len(self._samples) / self._window_s


class MetricsCollector:
    def __init__(
        self,
        *,
        window_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_s = window_s
        self._faces_per_frame = _Window(window_s, clock)
        self._detect_ms = _Window(window_s, clock)
        self._embed_ms = _Window(window_s, clock)
        self._batch_ms = _Window(window_s, clock)
        self._batches = _Window(window_s, clock)
        self._frames = _Window(window_s, clock)
        self._detected_frames = _Window(window_s, clock)
        self._detect_batch_size = _Window(window_s, clock)
        self._embed_batch_size = _Window(window_s, clock)
        self._active_tracks = 0

    def record_processed_frame(self) -> None:
        self._frames.add(1.0)

    def record_detection_batch(
        self,
        *,
        detect_s: float,
        embed_s: float,
        face_counts: Iterable[int],
        active_tracks: int,
        detect_n_frames: int,
        embed_n_crops: int,
    ) -> None:
        self._detect_ms.add(detect_s * 1000.0)
        self._embed_ms.add(embed_s * 1000.0)
        self._batch_ms.add((detect_s + embed_s) * 1000.0)
        self._batches.add(1.0)
        self._detect_batch_size.add(float(detect_n_frames))
        self._embed_batch_size.add(float(embed_n_crops))
        for count in face_counts:
            self._faces_per_frame.add(float(count))
            # One sample per frame actually run through detection, so its rate
            # is detected-frames/sec — same units as processed fps.
            self._detected_frames.add(1.0)
        self._active_tracks = active_tracks

    def snapshot(self) -> dict[str, float]:
        # Stride: processed frames per frame actually detected — how many video
        # frames the interpolator covers for each real detection.
        detected_fps = self._detected_frames.rate()
        processed_fps = self._frames.rate()
        stride = processed_fps / detected_fps if detected_fps > 0 else 0.0
        return {
            "detections_per_frame": round(self._faces_per_frame.mean(), 2),
            "detection_ms": round(self._detect_ms.mean(), 1),
            "embedding_ms": round(self._embed_ms.mean(), 1),
            "batch_ms": round(self._batch_ms.mean(), 1),
            "detection_hz": round(self._batches.rate(), 2),
            "detection_stride": round(stride, 1),
            "processed_fps": round(processed_fps, 1),
            "active_tracks": self._active_tracks,
            "detect_batch_size": round(self._detect_batch_size.mean(), 1),
            "embed_batch_size": round(self._embed_batch_size.mean(), 1),
            "window_s": self._window_s,
        }

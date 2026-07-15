"""Per-frame transform: timestamp overlay + real face detection and embedding."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from loguru import logger

from video_streamer.detection import Detection, FaceDetector, FaceEmbedder
from video_streamer.interpolation import LookaheadTrackBuffer
from video_streamer.metrics import MetricsCollector
from video_streamer.overlay import draw_dashed_rect, draw_overlay
from video_streamer.token_bucket import TokenBucket
from video_streamer.tracking import ByteTracker, TrackedFace


class PendingFrame(NamedTuple):
    """A pristine frame held back until the render cursor reaches it."""

    frame: np.ndarray
    captured_at: str


class SampledFrame(NamedTuple):
    """One pristine frame picked for a detection batch, with its index."""

    frame_index: int
    frame: np.ndarray


class FrameDetections(NamedTuple):
    """Detections + matching embeddings for one sampled frame."""

    frame_index: int
    detections: list[Detection]
    embeddings: list[np.ndarray]


@dataclass
class _BatchResult:
    """One detection pass: per-frame results plus how long each stage took."""

    per_frame: list[FrameDetections]
    detect_s: float
    embed_s: float
    detect_n_frames: int  # frames submitted to SCRFD
    embed_n_crops: int    # face crops submitted to ArcFace


# Upper bound on frames held back waiting for the interpolation cursor. The
# normal steady-state backlog is (lookahead + 2) detection intervals' worth of
# frames; this cap only bites when detection stalls badly.
_MAX_PENDING_FRAMES = 600


class Engine:
    def __init__(
        self,
        *,
        model_dir: Path,
        fps: float,
        scrfd_batch_frames: int = 4,
        arcface_batch_crops: int = 8,
        max_batch_lag_ms: float = 0.0,
        lookahead: int = 3,
    ) -> None:
        self._face_detector = FaceDetector(model_dir / "scrfd_10g_kps_dynamic.onnx")
        self._face_embedder = FaceEmbedder(model_dir / "arcface_w600k_r50_batch.onnx")
        self._scrfd_batch_frames = max(1, scrfd_batch_frames)
        self._arcface_batch_crops = max(1, arcface_batch_crops)
        self._max_batch_lag_ms = max(0.0, max_batch_lag_ms)
        self._frame_index = 0
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._fps = fps
        self._detection_buffer = LookaheadTrackBuffer(lookahead=lookahead, method="pchip")
        self._tracker = ByteTracker(fps)
        self._detection_task: asyncio.Future | None = None
        self._detection_started_at: float = 0.0
        # Newest frame index covered by the most recently scheduled batch; the
        # next batch samples the frames after it, giving gapless coverage.
        self._last_batch_max_idx: int = 0
        self._detection_budget = TokenBucket(capacity=1.0, refill_rate=fps)
        self._metrics = MetricsCollector()
        # Frames awaiting emission, keyed by frame index. The interpolation
        # buffer's render cursor trails the live frame by several detection
        # intervals; overlays are drawn on the frame they were computed for,
        # so frames are held back here until the cursor reaches them.
        self._pending_frames: dict[int, PendingFrame] = {}
        # True while emitting box-less fallback frames after a cap eviction;
        # gates the warning so a stall logs once, not once per frame.
        self._fallback_active = False
        # Monotonic timestamp of when the batch first became eligible (token
        # bucket passed, frames available) but wasn't full yet. Reset on fire
        # or when eligibility is lost.
        self._batch_eligible_since: float | None = None

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        *,
        model_dir: Path,
        fps: float,
        scrfd_batch_frames: int = 4,
        arcface_batch_crops: int = 8,
        max_batch_lag_ms: float = 0.0,
        lookahead: int = 3,
    ) -> AsyncIterator[Engine]:
        self = cls(
            model_dir=model_dir,
            fps=fps,
            scrfd_batch_frames=scrfd_batch_frames,
            arcface_batch_crops=arcface_batch_crops,
            max_batch_lag_ms=max_batch_lag_ms,
            lookahead=lookahead,
        )
        with self._face_detector, self._face_embedder:
            try:
                yield self
            finally:
                await self.aclose()

    async def process(self, frame: np.ndarray) -> np.ndarray | None:
        """Feed one live frame in; get the (delayed) overlaid frame out.

        Returns None while the lookahead buffer is still filling — the caller
        should simply skip writing in that case.
        """
        self._frame_index += 1
        self._metrics.record_processed_frame()
        self._buffer_frame(frame)
        self._collect_finished_batch()
        self._maybe_schedule_batch()
        return self._emit_delayed_frame()

    def _buffer_frame(self, frame: np.ndarray) -> None:
        """Hold the pristine frame back until the render cursor reaches it."""
        captured_at = datetime.now(UTC).strftime("%H:%M:%S")
        self._pending_frames[self._frame_index] = PendingFrame(frame, captured_at)
        if len(self._pending_frames) > _MAX_PENDING_FRAMES:
            evicted = next(iter(self._pending_frames))
            del self._pending_frames[evicted]
            logger.warning(
                "pending-frame buffer full, dropped frame {} — detection is falling behind",
                evicted,
            )

    def _collect_finished_batch(self) -> None:
        """Harvest a completed detection pass, if any.

        Tracking runs here, on the raw detections, so track ids are assigned
        before interpolation and the spline control points for a face all
        belong to that face.
        """
        if self._detection_task is None or not self._detection_task.done():
            return
        elapsed = time.monotonic() - self._detection_started_at
        self._detection_budget.record_spend(elapsed * self._fps)
        if not self._detection_task.cancelled():
            try:
                # One result per sampled frame, in ascending frame order.
                # Feed the tracker frame by frame so ByteTrack ids stay
                # stable, and stamp each snapshot with the frame it ran on
                # (not the later frame it finished on) or the interpolation
                # timeline shifts forward and boxes trail moving faces.
                batch = self._detection_task.result()
                face_counts: list[int] = []
                active_tracks = 0
                for frame_dets in batch.per_frame:
                    tracked = self._tracker.update(
                        frame_dets.detections, frame_dets.embeddings
                    )
                    self._detection_buffer.push(frame_dets.frame_index, tracked)
                    face_counts.append(len(frame_dets.detections))
                    active_tracks = len(tracked)
                self._metrics.record_detection_batch(
                    detect_s=batch.detect_s,
                    embed_s=batch.embed_s,
                    face_counts=face_counts,
                    active_tracks=active_tracks,
                    detect_n_frames=batch.detect_n_frames,
                    embed_n_crops=batch.embed_n_crops,
                )
            except Exception:
                logger.opt(exception=True).warning("detection failed")
        self._detection_task = None

    def _maybe_schedule_batch(self) -> None:
        """Kick off a new detection batch if the budget allows and none is
        already running.

        Waits until either the window has >= scrfd_batch_frames frames (batch
        full) or max_batch_lag_ms has elapsed since the batch first became
        eligible, then fires with whatever is available. max_batch_lag_ms=0
        fires immediately (current behaviour preserved).
        """
        if self._detection_task is not None:
            self._batch_eligible_since = None
            return
        if not self._detection_budget.try_acquire(1.0):
            self._batch_eligible_since = None
            return

        # Approximate window size (precise intersection with _pending_frames
        # is done inside _sample_batch_frames).
        window_size = self._frame_index - self._last_batch_max_idx
        if window_size <= 0:
            self._batch_eligible_since = None
            return

        now = time.monotonic()
        if self._batch_eligible_since is None:
            self._batch_eligible_since = now

        batch_full = window_size >= self._scrfd_batch_frames
        lag_exceeded = (now - self._batch_eligible_since) * 1000.0 >= self._max_batch_lag_ms

        if not batch_full and not lag_exceeded:
            return  # keep accumulating frames

        self._batch_eligible_since = None
        sampled = self._sample_batch_frames()
        if not sampled:
            return
        self._detection_started_at = now
        self._last_batch_max_idx = sampled[-1].frame_index
        loop = asyncio.get_running_loop()
        self._detection_task = loop.run_in_executor(
            self._executor, self._detect_batch, sampled
        )

    def _emit_delayed_frame(self) -> np.ndarray | None:
        """Advance the render cursor and emit the frame it points at, overlaid
        with that frame's (interpolated) detections. None while the lookahead
        buffer is still filling."""
        result = self._detection_buffer.get()
        if result is None:
            return None
        t_q, faces, is_interpolated = result

        # Emit the frame the coordinates were computed for. Frames older than
        # t_q are dropped; t_q itself is kept because the cursor re-emits it
        # while clamped waiting for lookahead.
        while self._pending_frames:
            oldest = next(iter(self._pending_frames))
            if oldest >= t_q:
                break
            del self._pending_frames[oldest]
        pending = self._pending_frames.get(t_q)
        if pending is None:
            # t_q was evicted by the _MAX_PENDING_FRAMES cap (detection is
            # stalling badly). Emit the oldest surviving frame without face
            # boxes rather than drawing t_q's coordinates on the wrong frame.
            if not self._pending_frames:
                return None
            if not self._fallback_active:
                self._fallback_active = True
                logger.warning(
                    "frame {} already evicted; emitting frames without boxes "
                    "until the render cursor catches up",
                    t_q,
                )
            pending = self._pending_frames[next(iter(self._pending_frames))]
            faces = []
        else:
            self._fallback_active = False

        # Single copy per emitted frame; the pristine original stays in
        # _pending_frames (t_q is re-emitted while the cursor is clamped, and
        # the detector must never see burned-in overlays).
        out_frame = pending.frame.copy()
        draw_overlay(out_frame, f"{pending.captured_at}  frame {t_q}")
        self._draw_detections(out_frame, faces, is_interpolated)
        return out_frame

    def _sample_batch_frames(self) -> list[SampledFrame]:
        """Pick up to N evenly spaced frames buffered since the last batch.

        Only frames still present in `_pending_frames` are eligible (older ones
        may already have been emitted and dropped). The newest frame is always
        included so consecutive batches stay contiguous.
        """
        window = [
            idx
            for idx in range(self._last_batch_max_idx + 1, self._frame_index + 1)
            if idx in self._pending_frames
        ]
        if not window:
            return []
        n = min(self._scrfd_batch_frames, len(window))
        positions = np.linspace(0, len(window) - 1, n)
        chosen = sorted({window[round(float(p))] for p in positions})
        return [SampledFrame(idx, self._pending_frames[idx].frame) for idx in chosen]

    def _detect_batch(self, sampled: list[SampledFrame]) -> _BatchResult:
        frames = [s.frame for s in sampled]
        detect_started = time.monotonic()
        dets_per_frame = self._face_detector.detect_batch(frames)
        detect_s = time.monotonic() - detect_started

        # Flatten every (frame, detection) pair into one ArcFace batch, then
        # split the embeddings back per source frame by detection count.
        items: list[tuple[np.ndarray, Detection]] = [
            (frame, det)
            for frame, dets in zip(frames, dets_per_frame, strict=True)
            for det in dets
        ]
        embed_started = time.monotonic()
        embeddings = self._face_embedder.embed_many(
            items, max_batch=self._arcface_batch_crops
        )
        embed_s = time.monotonic() - embed_started

        results: list[FrameDetections] = []
        cursor = 0
        for sample, dets in zip(sampled, dets_per_frame, strict=True):
            results.append(
                FrameDetections(
                    sample.frame_index, dets, embeddings[cursor : cursor + len(dets)]
                )
            )
            cursor += len(dets)
        return _BatchResult(
            per_frame=results,
            detect_s=detect_s,
            embed_s=embed_s,
            detect_n_frames=len(frames),
            embed_n_crops=len(items),
        )

    def metrics_snapshot(self) -> dict[str, float]:
        return self._metrics.snapshot()

    def _draw_detections(
        self,
        out: np.ndarray,
        tracked: list[TrackedFace],
        is_interpolated: bool,
    ) -> None:
        """Draw boxes/landmarks/ids in-place on `out` (already a copy)."""
        for face in tracked:
            det = face.detection
            emb = face.embedding
            color: tuple[int, int, int]
            if emb is not None:
                color = (
                    int((float(emb[0]) + 1.0) / 2.0 * 255),
                    int((float(emb[1]) + 1.0) / 2.0 * 255),
                    int((float(emb[2]) + 1.0) / 2.0 * 255),
                )
            else:
                color = (180, 180, 180)
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            if is_interpolated:
                draw_dashed_rect(out, (x1, y1), (x2, y2), color, thickness=2)
            else:
                cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            for lx, ly in det.landmarks:
                cv2.circle(out, (int(lx), int(ly)), 3, color, -1)
            cv2.putText(
                out,
                f"#{face.track_id}",
                (x1, max(y1 - 5, 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

    async def aclose(self) -> None:
        if self._detection_task is not None and not self._detection_task.done():
            self._detection_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._detection_task
        self._executor.shutdown(wait=False, cancel_futures=True)

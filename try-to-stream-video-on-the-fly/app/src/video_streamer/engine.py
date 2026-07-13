"""Per-frame transform: timestamp overlay + real face detection and embedding."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from video_streamer.detection import Detection, FaceDetector, FaceEmbedder
from video_streamer.interpolation import LookaheadTrackBuffer
from video_streamer.metrics import MetricsCollector
from video_streamer.overlay import draw_dashed_rect, draw_overlay
from video_streamer.token_bucket import TokenBucket
from video_streamer.tracking import ByteTracker, TrackedFace

logger = logging.getLogger(__name__)


@dataclass
class _BatchResult:
    """One detection pass: per-frame results plus how long each stage took."""

    per_frame: list[tuple[int, list[Detection], list[np.ndarray]]]
    detect_s: float
    embed_s: float

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
    ) -> None:
        self._face_detector = FaceDetector(model_dir / "scrfd_10g_kps_dynamic.onnx")
        self._face_embedder = FaceEmbedder(model_dir / "arcface_w600k_r50_batch.onnx")
        self._scrfd_batch_frames = max(1, scrfd_batch_frames)
        self._arcface_batch_crops = max(1, arcface_batch_crops)
        self._frame_index = 0
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._fps = fps
        self._detection_buffer = LookaheadTrackBuffer(lookahead=3, method="pchip")
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
        self._pending_frames: dict[int, tuple[np.ndarray, str]] = {}

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        *,
        model_dir: Path,
        fps: float,
        scrfd_batch_frames: int = 4,
        arcface_batch_crops: int = 8,
    ) -> AsyncIterator[Engine]:
        self = cls(
            model_dir=model_dir,
            fps=fps,
            scrfd_batch_frames=scrfd_batch_frames,
            arcface_batch_crops=arcface_batch_crops,
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
        captured_at = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._pending_frames[self._frame_index] = (frame, captured_at)
        if len(self._pending_frames) > _MAX_PENDING_FRAMES:
            evicted = next(iter(self._pending_frames))
            del self._pending_frames[evicted]
            logger.warning(
                "pending-frame buffer full, dropped frame %d — detection is falling behind",
                evicted,
            )

        # Collect completed detection result if ready. Tracking runs here, on
        # the raw detections, so track ids are assigned before interpolation
        # and the spline control points for a face all belong to that face.
        if self._detection_task is not None and self._detection_task.done():
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
                    for frame_idx, dets, embs in batch.per_frame:
                        tracked = self._tracker.update(dets, embs)
                        self._detection_buffer.push(frame_idx, tracked)
                        face_counts.append(len(dets))
                        active_tracks = len(tracked)
                    self._metrics.record_detection_batch(
                        detect_s=batch.detect_s,
                        embed_s=batch.embed_s,
                        face_counts=face_counts,
                        active_tracks=active_tracks,
                    )
                except Exception:
                    logger.warning("detection failed", exc_info=True)
            self._detection_task = None

        # Schedule a new detection batch if the budget allows and none is
        # already running. Sample up to N frames evenly spaced across the frames
        # buffered since the last batch, giving gapless real-detection coverage.
        # These frames are still pristine here (overlays are drawn on delayed
        # frames), so the detector never sees burned-in text.
        if self._detection_task is None and self._detection_budget.try_acquire(1.0):
            sampled = self._sample_batch_frames()
            if sampled:
                self._detection_started_at = time.monotonic()
                self._last_batch_max_idx = sampled[-1][0]
                loop = asyncio.get_running_loop()
                self._detection_task = loop.run_in_executor(
                    self._executor, self._detect_batch, sampled
                )

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
        entry = self._pending_frames.get(t_q)
        if entry is None:
            if not self._pending_frames:
                return None
            entry = self._pending_frames[next(iter(self._pending_frames))]
        out_frame, frame_captured_at = entry

        if faces:
            out_frame = draw_overlay(out_frame, f"{frame_captured_at}  frame {t_q}")
        return self._draw_detections(out_frame, faces, is_interpolated)

    def _sample_batch_frames(self) -> list[tuple[int, np.ndarray]]:
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
        chosen = sorted({window[int(round(p))] for p in positions})
        return [(idx, self._pending_frames[idx][0]) for idx in chosen]

    def _detect_batch(
        self, frames_with_idx: list[tuple[int, np.ndarray]]
    ) -> _BatchResult:
        frames = [frame for _, frame in frames_with_idx]
        detect_started = time.monotonic()
        dets_per_frame = self._face_detector.detect_batch(frames)
        detect_s = time.monotonic() - detect_started

        # Flatten every (frame, detection) pair into one ArcFace batch, then
        # split the embeddings back per source frame by detection count.
        items: list[tuple[np.ndarray, Detection]] = [
            (frame, det)
            for frame, dets in zip(frames, dets_per_frame)
            for det in dets
        ]
        embed_started = time.monotonic()
        embeddings = self._face_embedder.embed_many(
            items, max_batch=self._arcface_batch_crops
        )
        embed_s = time.monotonic() - embed_started

        results: list[tuple[int, list[Detection], list[np.ndarray]]] = []
        cursor = 0
        for (idx, _), dets in zip(frames_with_idx, dets_per_frame):
            results.append((idx, dets, embeddings[cursor : cursor + len(dets)]))
            cursor += len(dets)
        return _BatchResult(per_frame=results, detect_s=detect_s, embed_s=embed_s)

    def metrics_snapshot(self) -> dict[str, float]:
        return self._metrics.snapshot()

    def _draw_detections(
        self,
        frame: np.ndarray,
        tracked: list[TrackedFace],
        is_interpolated: bool,
    ) -> np.ndarray:
        out = frame.copy()
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
        return out

    async def aclose(self) -> None:
        if self._detection_task is not None and not self._detection_task.done():
            self._detection_task.cancel()
            try:
                await self._detection_task
            except (asyncio.CancelledError, Exception):
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

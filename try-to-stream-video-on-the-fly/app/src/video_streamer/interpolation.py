"""Per-track interpolation of detection snapshots with lookahead buffering.

Detection runs much less often than the video frame rate, so this module
maintains a render cursor that advances independently of the live frame index.
The cursor always stays within a detection segment that has `lookahead` future
snapshots available, guaranteeing pure interpolation (never extrapolation).

Faces are matched across snapshots by tracker id, not list position —
detection order is confidence order, so positional matching would blend
coordinates of different faces whenever confidence ranks flip or a face
enters/leaves the frame.

Because of the lookahead, the returned coordinates describe a frame several
detection intervals in the past. The caller must delay the video frames by the
same amount (`get()` returns the frame index to pair with) or the overlay will
trail moving faces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline, PchipInterpolator

from video_streamer.detection import Detection
from video_streamer.tracking import TrackedFace


InterpolationMethod = Literal["cubic", "pchip", "linear"]


def _interp(vts: NDArray, ys: NDArray, t_q: float, method: InterpolationMethod) -> NDArray:
    if method == "pchip":
        return PchipInterpolator(vts, ys)(t_q)
    if method == "linear":
        return np.array([np.interp(t_q, vts, ys[:, k]) for k in range(ys.shape[1])])
    return CubicSpline(vts, ys)(t_q)


@dataclass
class Snapshot:
    frame_idx: int
    faces: dict[int, TrackedFace]  # keyed by track_id


class LookaheadTrackBuffer:
    """Per-video-frame spline interpolation of tracked faces with a fixed lookahead lag.

    Call `push()` each time a tracked detection result arrives. Call `get()`
    once per video frame — it advances an internal render cursor by one frame
    each time, staying within the current target segment (the earliest one that
    still has `lookahead` future snapshots as right-hand control points).
    """

    def __init__(self, lookahead: int = 3, method: InterpolationMethod = "pchip") -> None:
        self._lookahead = lookahead
        self._method = method
        self._snapshots: list[Snapshot] = []
        self._seg_idx: int = 0        # index of the current segment's start snapshot
        self._render_cursor: float | None = None

    def push(self, frame_idx: int, faces: list[TrackedFace]) -> None:
        self._snapshots.append(Snapshot(frame_idx, {f.track_id: f for f in faces}))

    @property
    def ready(self) -> bool:
        # Need the segment end (seg_idx+1) plus `lookahead` more snapshots ahead of it.
        return len(self._snapshots) > self._seg_idx + self._lookahead + 1

    def get(self) -> tuple[int, list[TrackedFace], bool] | None:
        """Return (frame_idx, interpolated faces, is_interpolated) for the current cursor.

        Each call advances the internal render cursor by one frame. Returns
        None while the buffer is still accumulating its initial lookahead.
        The frame_idx names the (past) video frame the coordinates belong to;
        the caller should render onto that frame, not the live one.
        is_interpolated is True when the frame lies strictly between two
        detection snapshots, False when it lands exactly on one.
        """
        if not self.ready:
            return None

        seg_start = self._snapshots[self._seg_idx]
        seg_end = self._snapshots[self._seg_idx + 1]

        if self._render_cursor is None:
            self._render_cursor = float(seg_start.frame_idx)

        # Clamp to current segment for pure interpolation.
        t_q = int(np.clip(self._render_cursor, seg_start.frame_idx, seg_end.frame_idx))
        is_interpolated = t_q not in (seg_start.frame_idx, seg_end.frame_idx)
        self._render_cursor += 1.0

        # Advance to the next segment once the cursor leaves the current one,
        # but only when we still have enough lookahead beyond the new segment end.
        while (
            self._render_cursor > self._snapshots[self._seg_idx + 1].frame_idx
            and len(self._snapshots) > self._seg_idx + self._lookahead + 2
        ):
            self._seg_idx += 1

        # Snapshots behind the spline window can never be used again.
        drop = self._seg_idx - self._lookahead
        if drop > 0:
            del self._snapshots[:drop]
            self._seg_idx -= drop

        # Cap the cursor at the newest frame we can still interpolate (the one
        # with `lookahead` snapshots after it). Detection results arrive in
        # bursts — many snapshots at once, then a gap — so without this cap the
        # cursor free-runs during the gap (t_q is clamped, but render_cursor
        # keeps incrementing), accruing "debt" that lurches forward when the
        # next burst lands: a visible overlay jump. Holding the cursor here
        # instead makes it resume smoothly, one frame at a time.
        newest = len(self._snapshots) - self._lookahead - 1
        if newest >= 1:
            cap = float(self._snapshots[newest].frame_idx)
            if self._render_cursor > cap:
                self._render_cursor = cap

        # Build the spline window: up to `lookahead` snapshots on each side of
        # the segment being rendered.
        w_start = max(0, self._seg_idx - self._lookahead)
        w_end = min(len(self._snapshots), self._seg_idx + self._lookahead + 2)
        window = self._snapshots[w_start:w_end]

        faces: list[TrackedFace] = []
        for tid, face in seg_start.faces.items():
            points = [(s.frame_idx, s.faces[tid]) for s in window if tid in s.faces]
            if len(points) < 2:
                faces.append(face)
                continue

            vts = np.array([idx for idx, _ in points], dtype=np.float64)
            bboxes = np.array(
                [f.detection.bbox for _, f in points], dtype=np.float64
            )  # (N, 4)
            lms = np.array(
                [f.detection.landmarks for _, f in points], dtype=np.float64
            ).reshape(len(points), -1)  # (N, 10)

            ibbox: NDArray[np.float64] = _interp(vts, bboxes, t_q, self._method)
            ilms: NDArray[np.float64] = _interp(vts, lms, t_q, self._method).reshape(-1, 2)

            faces.append(
                TrackedFace(
                    track_id=tid,
                    detection=Detection(
                        bbox=(ibbox[0], ibbox[1], ibbox[2], ibbox[3]),
                        landmarks=ilms.astype(np.float32),
                        confidence=face.detection.confidence,
                    ),
                    embedding=face.embedding,
                )
            )

        return t_q, faces, is_interpolated

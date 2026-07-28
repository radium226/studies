"""ByteTrack multi-object tracker wrapper for face detections (via trackers + supervision)."""

from __future__ import annotations

import numpy as np
import supervision as sv
from numpy.typing import NDArray
from trackers import ByteTrackTracker as _ByteTrackTracker
from trackers.utils.iou import BIoU

from video_analyzer import kernel


def _xyxy(bounding_box: kernel.BoundingBox) -> tuple[float, float, float, float]:
    return (
        bounding_box.x,
        bounding_box.y,
        bounding_box.x + bounding_box.width,
        bounding_box.y + bounding_box.height,
    )


class ByteTrackTracker(kernel.Tracker[NDArray[np.float32]]):
    def __init__(self, fps: float, *, lost_track_buffer: int = 30) -> None:
        self._tracker = _ByteTrackTracker(
            frame_rate=fps,
            lost_track_buffer=lost_track_buffer,
            minimum_consecutive_frames=1,
            track_activation_threshold=0.25,
            # The tracker is updated at detection cadence (a few Hz), not video
            # frame rate, so a fast face can move nearly its own width between
            # updates — plain IoU association sees zero overlap and churns
            # track ids. Buffered IoU expands boxes before matching to bridge
            # that gap.
            iou=BIoU(buffer_ratio=0.5),
        )

    async def update(
        self,
        faces: list[kernel.Face[NDArray[np.float32]]],
    ) -> list[kernel.TrackedFace[NDArray[np.float32]]]:
        # ByteTrack's update is cheap bookkeeping (no inference), so this can
        # run inline on the event loop — no executor offload needed.
        if not faces:
            return []

        sv_dets = sv.Detections(
            xyxy=np.array(
                [_xyxy(face.detection.bounding_box) for face in faces], dtype=np.float32
            ),
            confidence=np.array(
                [face.detection.confidence for face in faces], dtype=np.float32
            ),
        )
        tracked = self._tracker.update(sv_dets)

        result: list[kernel.TrackedFace[NDArray[np.float32]]] = []
        if tracked.tracker_id is None:
            return result

        # ByteTrack gives us stable ids, but its boxes are Kalman estimates
        # that drift from the actual detection. Draw the *matched* input
        # detection instead, so the box and its landmarks stay mutually
        # consistent and aligned with the face; use the tracker only for the id.
        for i, tid in enumerate(tracked.tracker_id):
            if tid < 0:  # skip unconfirmed tracks
                continue
            face_idx = self._best_match(tracked.xyxy[i], faces)
            if face_idx is None:
                continue
            result.append(kernel.TrackedFace(track_id=int(tid), face=faces[face_idx]))
        return result

    @staticmethod
    def _best_match(
        track_box: NDArray[np.float32],
        faces: list[kernel.Face[NDArray[np.float32]]],
    ) -> int | None:
        """Index of the input face with the highest IoU against a track box."""
        tx1, ty1, tx2, ty2 = (float(v) for v in track_box)
        best_idx: int | None = None
        best_iou = 0.0
        for idx, face in enumerate(faces):
            dx1, dy1, dx2, dy2 = _xyxy(face.detection.bounding_box)
            ix1, iy1 = max(tx1, dx1), max(ty1, dy1)
            ix2, iy2 = min(tx2, dx2), min(ty2, dy2)
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            if inter <= 0.0:
                continue
            union = (tx2 - tx1) * (ty2 - ty1) + (dx2 - dx1) * (dy2 - dy1) - inter
            iou = inter / union if union > 0.0 else 0.0
            if iou > best_iou:
                best_iou, best_idx = iou, idx
        return best_idx

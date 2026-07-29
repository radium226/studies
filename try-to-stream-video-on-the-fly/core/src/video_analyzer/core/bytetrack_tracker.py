"""ByteTrack multi-object tracker wrapper for face detections (via trackers + supervision)."""

from __future__ import annotations

import numpy as np
import supervision as sv
from numpy.typing import NDArray
from trackers import ByteTrackTracker as _VendoredByteTrackTracker
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
    """`kernel.Tracker` backed by ByteTrack.

    A note on `fps`: ByteTrack counts time in *updates*, but this tracker is
    stepped once per detection snapshot (a few Hz), not once per video frame.
    `lost_track_buffer` therefore expires after that many missed *detection
    passes* — its wall-clock lifetime is `lost_track_buffer / actual detection
    rate`, not `/ fps`. `fps` is only forwarded as ByteTrack's `frame_rate`
    bookkeeping parameter.
    """

    def __init__(
        self,
        fps: float,
        *,
        lost_track_buffer: int = 30,
        track_activation_threshold: float = 0.25,
        minimum_consecutive_frames: int = 1,
        iou_buffer_ratio: float = 0.5,
    ) -> None:
        self._bytetrack = _VendoredByteTrackTracker(
            frame_rate=fps,
            lost_track_buffer=lost_track_buffer,
            minimum_consecutive_frames=minimum_consecutive_frames,
            track_activation_threshold=track_activation_threshold,
            # The tracker is updated at detection cadence (a few Hz), not video
            # frame rate, so a fast face can move nearly its own width between
            # updates — plain IoU association sees zero overlap and churns
            # track ids. Buffered IoU expands boxes before matching to bridge
            # that gap.
            iou=BIoU(buffer_ratio=iou_buffer_ratio),
        )

    async def update(
        self,
        faces: list[kernel.Face[NDArray[np.float32]]],
    ) -> list[kernel.TrackedFace[NDArray[np.float32]]]:
        # ByteTrack's update is cheap bookkeeping (no inference), so this can
        # run inline on the event loop — no executor offload needed.
        if not faces:
            return []

        supervision_detections = sv.Detections(
            xyxy=np.array(
                [_xyxy(face.detection.bounding_box) for face in faces], dtype=np.float32
            ),
            confidence=np.array(
                [face.detection.confidence for face in faces], dtype=np.float32
            ),
        )
        tracked = self._bytetrack.update(supervision_detections)

        result: list[kernel.TrackedFace[NDArray[np.float32]]] = []
        if tracked.tracker_id is None:
            return result

        # ByteTrack gives us stable ids, but its boxes are Kalman estimates
        # that drift from the actual detection. Draw the *matched* input
        # detection instead, so the box and its landmarks stay mutually
        # consistent and aligned with the face; use the tracker only for the
        # id. Each input face is claimed at most once (`matched`), so two
        # track boxes can never emit the same face under different ids.
        matched: set[int] = set()
        for i, tracker_id in enumerate(tracked.tracker_id):
            if tracker_id < 0:  # skip unconfirmed tracks
                continue
            face_idx = self._best_iou_match(tracked.xyxy[i], faces, exclude=matched)
            if face_idx is None:
                continue
            matched.add(face_idx)
            result.append(
                kernel.TrackedFace(track_id=int(tracker_id), face=faces[face_idx])
            )
        return result

    async def reset(self) -> None:
        # Scene cut: identities never survive it. The vendored tracker's own
        # reset drops every live and lost track.
        self._bytetrack.reset()

    @staticmethod
    def _best_iou_match(
        track_box: NDArray[np.float32],
        faces: list[kernel.Face[NDArray[np.float32]]],
        exclude: set[int] | None = None,
    ) -> int | None:
        """Index of the not-yet-claimed input face with the highest IoU
        against a track box."""
        track_x1, track_y1, track_x2, track_y2 = (float(v) for v in track_box)
        best_idx: int | None = None
        best_iou = 0.0
        for idx, face in enumerate(faces):
            if exclude is not None and idx in exclude:
                continue
            det_x1, det_y1, det_x2, det_y2 = _xyxy(face.detection.bounding_box)
            ix1, iy1 = max(track_x1, det_x1), max(track_y1, det_y1)
            ix2, iy2 = min(track_x2, det_x2), min(track_y2, det_y2)
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            if inter <= 0.0:
                continue
            union = (
                (track_x2 - track_x1) * (track_y2 - track_y1)
                + (det_x2 - det_x1) * (det_y2 - det_y1)
                - inter
            )
            iou = inter / union if union > 0.0 else 0.0
            if iou > best_iou:
                best_iou, best_idx = iou, idx
        return best_idx

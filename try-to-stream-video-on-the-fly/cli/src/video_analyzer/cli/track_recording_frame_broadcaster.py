"""A real `FrameBroadcaster` (`core` deliberately ships none — see `core/CLAUDE.md`): buffers a
fixed-size crop of each tracked face, per `track_id`, so the CLI can play each track back through
its own `ffplay` window once the main video is done (see `main.py`'s `--play-tracks`)."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel


def _crop_face(
    frame: NDArray[np.uint8], bounding_box: kernel.BoundingBox, crop_size: int
) -> NDArray[np.uint8]:
    x1 = max(0, int(bounding_box.x))
    y1 = max(0, int(bounding_box.y))
    x2 = min(frame.shape[1], int(bounding_box.x + bounding_box.width))
    y2 = min(frame.shape[0], int(bounding_box.y + bounding_box.height))
    if x2 <= x1 or y2 <= y1:
        return np.zeros((crop_size, crop_size, 3), dtype=np.uint8)
    return cast(
        "NDArray[np.uint8]", cv2.resize(frame[y1:y2, x1:x2], (crop_size, crop_size))
    )


class TrackRecordingFrameBroadcaster(
    kernel.FrameBroadcaster[NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]]
):
    def __init__(self, crop_size: int = 160) -> None:
        self._crop_size = crop_size
        self.crops_by_track: dict[int, list[NDArray[np.uint8]]] = {}

    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        frame_content = annotated_frame.frame.content
        for tracked_face in annotated_frame.faces:
            crop = _crop_face(
                frame_content, tracked_face.face.detection.bounding_box, self._crop_size
            )
            self.crops_by_track.setdefault(tracked_face.track_id, []).append(crop)

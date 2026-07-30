"""Pins that `TrackRecordingFrameBroadcaster` records a crop for *every*
rendered frame a track appears in — interpolated frames (`is_exact=False`,
bracketed) and held frames (no bracket) as much as exact detections — cropped
at each frame's own (possibly interpolated) bounding box. This is what makes a
`--play-tracks` replay show smooth motion instead of only the sparse detection
hits."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel
from video_analyzer.cli.config import TrackRecordingFrameBroadcasterConfig
from video_analyzer.cli.track_recording_frame_broadcaster import (
    TrackRecordingFrameBroadcaster,
)

_CROP_SIZE = 16


def _tracked_face(
    track_id: int, x: float, y: float
) -> kernel.TrackedFace[NDArray[np.float32]]:
    return kernel.TrackedFace(
        track_id=track_id,
        face=kernel.Face(
            detection=kernel.Detection(
                bounding_box=kernel.BoundingBox(x=x, y=y, width=10.0, height=10.0),
                landmarks=kernel.FaceLandmarks(
                    left_eye=(0.0, 0.0),
                    right_eye=(0.0, 0.0),
                    nose=(0.0, 0.0),
                    mouth_left=(0.0, 0.0),
                    mouth_right=(0.0, 0.0),
                ),
                confidence=1.0,
            ),
            embedding=np.zeros(4, dtype=np.float32),
        ),
    )


def _snapshot(
    frame_index: int, faces: list[kernel.TrackedFace[NDArray[np.float32]]]
) -> kernel.Snapshot[kernel.TrackedFace[NDArray[np.float32]]]:
    return kernel.Snapshot(frame_index=frame_index, faces=faces, is_scene_start=False)


def _frame_with_patch(index: int, x: int, y: int) -> kernel.Frame[NDArray[np.uint8]]:
    """A black frame with a solid white 10x10 patch at (x, y) — where the
    face's bounding box points — so the recorded crop's content proves which
    box was actually cropped."""
    content = np.zeros((60, 60, 3), dtype=np.uint8)
    content[y : y + 10, x : x + 10] = 255
    return kernel.Frame(index=index, content=content)


async def test_records_interpolated_frames_at_their_interpolated_box() -> None:
    recorder = TrackRecordingFrameBroadcaster(
        config=TrackRecordingFrameBroadcasterConfig(crop_size=_CROP_SIZE)
    )
    start = _tracked_face(1, x=10.0, y=20.0)
    end = _tracked_face(1, x=30.0, y=20.0)
    interpolated = _tracked_face(1, x=20.0, y=20.0)

    await recorder.broadcast_frame(
        kernel.AnnotatedFrame(
            frame=_frame_with_patch(5, x=20, y=20),
            faces=[interpolated],
            interpolation_bracket=(_snapshot(0, [start]), _snapshot(10, [end])),
            is_exact=False,
        )
    )

    crops = recorder.crops_by_track[1]
    assert len(crops) == 1
    assert crops[0].shape == (_CROP_SIZE, _CROP_SIZE, 3)
    assert (crops[0] == 255).all()


async def test_records_held_frames_too() -> None:
    recorder = TrackRecordingFrameBroadcaster(
        config=TrackRecordingFrameBroadcasterConfig(crop_size=_CROP_SIZE)
    )
    held = _tracked_face(1, x=10.0, y=20.0)

    await recorder.broadcast_frame(
        kernel.AnnotatedFrame(
            frame=_frame_with_patch(6, x=10, y=20),
            faces=[held],
            interpolation_bracket=None,
            is_exact=False,
        )
    )

    assert len(recorder.crops_by_track[1]) == 1
    assert (recorder.crops_by_track[1][0] == 255).all()

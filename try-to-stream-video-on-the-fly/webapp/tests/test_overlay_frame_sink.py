"""Drawing-only tests for `OverlayFrameSink`'s helpers — no ffmpeg encoder involved.

`_draw_detections` mutates a plain numpy canvas, so it's exercised directly rather than through
the sink (whose `write_frame` would need a real `core.FfmpegFrameSink` behind it).
"""

from __future__ import annotations

import numpy as np

from video_analyzer import kernel
from video_analyzer.webapp.overlay_frame_sink import _draw_detections, _track_color


def _embedding(*values: float) -> np.ndarray:
    """An L2-normalized 512-d vector shaped like a real ArcFace output, with the leading
    dimensions set explicitly."""
    vector = np.zeros(512, dtype=np.float32)
    vector[: len(values)] = values
    return vector / np.linalg.norm(vector)


def _tracked_face(
    track_id: str = "1",
    *,
    x: float = 10.0,
    y: float = 10.0,
    embedding: np.ndarray | None = None,
) -> kernel.TrackedFace[np.ndarray]:
    return kernel.TrackedFace(
        track_id=track_id,
        face=kernel.Face(
            detection=kernel.Detection(
                bounding_box=kernel.BoundingBox(x=x, y=y, width=40.0, height=40.0),
                landmarks=kernel.FaceLandmarks(
                    left_eye=(x + 10.0, y + 12.0),
                    right_eye=(x + 28.0, y + 12.0),
                    nose=(x + 19.0, y + 22.0),
                    mouth_left=(x + 12.0, y + 30.0),
                    mouth_right=(x + 26.0, y + 30.0),
                ),
                confidence=0.9,
            ),
            embedding=embedding if embedding is not None else _embedding(1.0, 0.0),
        ),
    )


def test_track_color_is_stable_for_one_embedding() -> None:
    embedding = _embedding(0.3, -0.7)
    assert _track_color(embedding) == _track_color(embedding.copy())


def test_track_color_separates_identities_instead_of_collapsing_to_grey() -> None:
    # The regression this guards: mapping L2-normalized components straight onto BGR (app/'s
    # original formula) makes every identity a near-identical mid-grey, because each of 512
    # components averages ~0.04. These four should be visibly different colours.
    colors = [
        _track_color(_embedding(1.0, 0.0)),
        _track_color(_embedding(0.0, 1.0)),
        _track_color(_embedding(-1.0, 0.0)),
        _track_color(_embedding(0.0, -1.0)),
    ]
    assert len(set(colors)) == 4
    for color in colors:
        # Full saturation means one channel is pinned at 0 and another at 255; a grey would have
        # all three within a few units of each other.
        assert max(color) - min(color) == 255


def test_track_color_falls_back_when_there_is_no_usable_embedding() -> None:
    assert _track_color(np.zeros(1, dtype=np.float32)) == (180, 180, 180)


def test_draws_landmarks_as_filled_points() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    face = _tracked_face()

    _draw_detections(frame, [face], is_exact=True)

    # Each of the 5 keypoints becomes a filled disc centred on its own coordinates.
    for x, y in (
        face.face.detection.landmarks.left_eye,
        face.face.detection.landmarks.right_eye,
        face.face.detection.landmarks.nose,
        face.face.detection.landmarks.mouth_left,
        face.face.detection.landmarks.mouth_right,
    ):
        assert frame[int(y), int(x)].any(), f"no landmark drawn at {(x, y)}"


def test_box_and_landmarks_share_the_identity_colour() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    face = _tracked_face(embedding=_embedding(0.0, 1.0))

    _draw_detections(frame, [face], is_exact=True)

    color = _track_color(face.face.embedding)
    box = face.face.detection.bounding_box
    # Top-left corner of a solid box, and the nose keypoint.
    assert tuple(int(c) for c in frame[int(box.y), int(box.x)]) == color
    nose_x, nose_y = face.face.detection.landmarks.nose
    assert tuple(int(c) for c in frame[int(nose_y), int(nose_x)]) == color


def test_two_tracks_are_drawn_in_different_colours() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    first = _tracked_face("1", x=5.0, y=5.0, embedding=_embedding(1.0, 0.0))
    second = _tracked_face("2", x=50.0, y=50.0, embedding=_embedding(-1.0, 0.2))

    _draw_detections(frame, [first, second], is_exact=True)

    assert tuple(frame[5, 5]) != tuple(frame[50, 50])


def test_interpolated_frames_get_a_dashed_box() -> None:
    exact_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    dashed_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    face = _tracked_face()

    _draw_detections(exact_frame, [face], is_exact=True)
    _draw_detections(dashed_frame, [face], is_exact=False)

    # A dashed box paints strictly fewer pixels than the solid one it replaces.
    assert dashed_frame.any()
    assert np.count_nonzero(dashed_frame) < np.count_nonzero(exact_frame)

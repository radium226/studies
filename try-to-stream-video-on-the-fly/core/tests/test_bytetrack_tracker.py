"""ByteTrackTracker: the pure IoU re-association logic, plus stable track ids
across calls. No ONNX/ffmpeg needed — trackers/supervision are lightweight
numpy-based dependencies."""

import numpy as np

from video_analyzer import kernel
from video_analyzer.core.bytetrack_tracker import ByteTrackTracker


def _face(x: float, y: float, width: float, height: float) -> kernel.Face[np.ndarray]:
    return kernel.Face(
        detection=kernel.Detection(
            bounding_box=kernel.BoundingBox(x=x, y=y, width=width, height=height),
            landmarks=kernel.FaceLandmarks(
                left_eye=(0.0, 0.0),
                right_eye=(0.0, 0.0),
                nose=(0.0, 0.0),
                mouth_left=(0.0, 0.0),
                mouth_right=(0.0, 0.0),
            ),
            confidence=1.0,
        ),
        embedding=np.zeros(1, dtype=np.float32),
    )


def test_best_match_picks_highest_iou() -> None:
    faces = [_face(0, 0, 10, 10), _face(100, 100, 10, 10)]
    track_box = np.array([1.0, 1.0, 11.0, 11.0], dtype=np.float32)
    assert ByteTrackTracker._best_iou_match(track_box, faces) == 0


def test_best_match_returns_none_when_no_overlap() -> None:
    faces = [_face(0, 0, 10, 10)]
    track_box = np.array([100.0, 100.0, 110.0, 110.0], dtype=np.float32)
    assert ByteTrackTracker._best_iou_match(track_box, faces) is None


def test_best_match_empty_faces() -> None:
    track_box = np.array([0.0, 0.0, 10.0, 10.0], dtype=np.float32)
    assert ByteTrackTracker._best_iou_match(track_box, []) is None


async def test_update_assigns_stable_track_id_across_calls() -> None:
    # ByteTrack needs a couple of consecutive observations before it confirms
    # a track (the first is tentative and comes back with tracker_id < 0,
    # which ByteTrackTracker.update filters out) — feed it several identical
    # detections and check the id stays put once a track is confirmed.
    tracker = ByteTrackTracker(fps=30.0)
    face = _face(0, 0, 10, 10)

    results = [await tracker.update([face]) for _ in range(5)]
    confirmed = [tracked[0].track_id for tracked in results if tracked]

    assert confirmed
    assert len(set(confirmed)) == 1


async def test_update_empty_input() -> None:
    tracker = ByteTrackTracker(fps=30.0)
    assert await tracker.update([]) == []


def test_best_match_skips_excluded_faces() -> None:
    faces = [_face(0, 0, 10, 10), _face(2, 2, 10, 10)]
    track_box = np.array([1.0, 1.0, 11.0, 11.0], dtype=np.float32)
    assert ByteTrackTracker._best_iou_match(track_box, faces, exclude={0}) == 1


async def test_each_face_is_claimed_by_at_most_one_track() -> None:
    # Two well-separated faces tracked long enough to confirm both, then a
    # single face left: only one track may claim it.
    tracker = ByteTrackTracker(fps=30.0)
    left, right = _face(0, 0, 10, 10), _face(100, 0, 10, 10)
    for _ in range(5):
        await tracker.update([left, right])

    tracked = await tracker.update([left])

    assert len(tracked) <= 1


async def test_reset_forgets_confirmed_tracks() -> None:
    tracker = ByteTrackTracker(fps=30.0)
    face = _face(0, 0, 10, 10)
    for _ in range(5):
        await tracker.update([face])
    assert await tracker.update([face])  # confirmed by now

    await tracker.reset()

    # A fresh tracker's first observation is tentative again (filtered out),
    # proving the pre-reset confirmation didn't survive.
    assert await tracker.update([face]) == []

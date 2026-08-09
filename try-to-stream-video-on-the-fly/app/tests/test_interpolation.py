"""LookaheadTrackBuffer: cursor pacing, segment advancement, id matching."""

import numpy as np

from video_streamer.detection import Detection
from video_streamer.interpolation import LookaheadTrackBuffer
from video_streamer.tracking import TrackedFace


def face(tid: int, x: float) -> TrackedFace:
    return TrackedFace(
        track_id=tid,
        detection=Detection(
            bbox=(x, 0.0, x + 10.0, 10.0),
            landmarks=np.zeros((5, 2), dtype=np.float32),
            confidence=0.9,
        ),
        embedding=None,
    )


def test_returns_none_until_lookahead_filled() -> None:
    buf = LookaheadTrackBuffer(lookahead=1, method="linear")
    buf.push(0, [face(1, 0.0)])
    assert buf.get() is None
    buf.push(5, [face(1, 10.0)])
    assert buf.get() is None  # needs seg end + `lookahead` beyond it
    buf.push(10, [face(1, 20.0)])
    result = buf.get()
    assert result is not None
    t_q, _faces, is_interpolated = result
    assert t_q == 0
    assert not is_interpolated  # cursor sits exactly on a real detection


def test_cursor_advances_one_frame_per_call_and_interpolates_linearly() -> None:
    # x moves at exactly 2 px/frame across snapshots, so linear interpolation
    # must reproduce x(t) = 2t at every intermediate frame.
    buf = LookaheadTrackBuffer(lookahead=1, method="linear")
    for idx in (0, 5, 10):
        buf.push(idx, [face(1, 2.0 * idx)])
    for expected_t in range(5):
        t_q, faces, is_interpolated = buf.get()
        assert t_q == expected_t
        assert is_interpolated == (expected_t not in (0, 5))
        assert faces[0].detection.bbox[0] == expected_t * 2.0


def test_cursor_holds_at_cap_during_snapshot_gap_then_resumes() -> None:
    # Detection results arrive in bursts; between bursts the cursor must hold
    # at the newest interpolatable frame instead of accruing a jump.
    buf = LookaheadTrackBuffer(lookahead=1, method="linear")
    for idx in (0, 5, 10):
        buf.push(idx, [face(1, 2.0 * idx)])
    seen = [buf.get()[0] for _ in range(10)]
    assert seen == [0, 1, 2, 3, 4, 5, 5, 5, 5, 5]
    buf.push(15, [face(1, 30.0)])
    assert buf.get()[0] == 5  # one more re-emit while the segment advances
    assert [buf.get()[0] for _ in range(5)] == [6, 7, 8, 9, 10]


def test_faces_matched_by_track_id_not_position() -> None:
    # The two faces swap list order between snapshots; matching by position
    # would blend id 1 toward id 2's coordinates.
    buf = LookaheadTrackBuffer(lookahead=1, method="linear")
    buf.push(0, [face(1, 0.0), face(2, 100.0)])
    buf.push(5, [face(2, 110.0), face(1, 10.0)])
    buf.push(10, [face(2, 120.0), face(1, 20.0)])
    buf.get()  # t_q=0
    t_q, faces, _ = buf.get()
    assert t_q == 1
    by_id = {f.track_id: f.detection.bbox[0] for f in faces}
    assert by_id == {1: 2.0, 2: 102.0}


def test_single_snapshot_track_falls_back_to_raw_detection() -> None:
    buf = LookaheadTrackBuffer(lookahead=1, method="linear")
    buf.push(0, [face(1, 0.0), face(9, 500.0)])  # id 9 never seen again
    buf.push(5, [face(1, 10.0)])
    buf.push(10, [face(1, 20.0)])
    _, faces, _ = buf.get()
    by_id = {f.track_id: f.detection.bbox[0] for f in faces}
    assert by_id[9] == 500.0  # emitted as-is, not interpolated

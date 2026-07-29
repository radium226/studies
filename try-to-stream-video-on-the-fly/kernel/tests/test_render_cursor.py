import asyncio
from collections.abc import Coroutine
from typing import Any

from video_analyzer import kernel
from video_analyzer.kernel.render_cursor import AdvanceResult, RenderCursor

from .fake import Interpolator, TrackedFace


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _tracked_face(x: float, track_id: int = 0) -> TrackedFace:
    return kernel.TrackedFace(
        track_id=track_id,
        face=kernel.Face(
            detection=kernel.Detection(
                bounding_box=kernel.BoundingBox(x=x, y=0.0, width=10.0, height=10.0),
                landmarks=kernel.FaceLandmarks(
                    left_eye=(0.0, 0.0),
                    right_eye=(1.0, 0.0),
                    nose=(0.5, 0.5),
                    mouth_left=(0.0, 1.0),
                    mouth_right=(1.0, 1.0),
                ),
                confidence=1.0,
            ),
            embedding=0,
        ),
    )


def _snapshot(frame_index: int, x: float | None = None) -> kernel.Snapshot[TrackedFace]:
    return kernel.Snapshot(
        frame_index=frame_index,
        faces=[_tracked_face(x if x is not None else float(frame_index))],
    )


def _cursor(lookahead: int) -> RenderCursor[int]:
    return RenderCursor(lookahead, Interpolator())


async def _drain(cursor: RenderCursor[int]) -> list[AdvanceResult[int]]:
    results: list[AdvanceResult[int]] = []
    while (result := await cursor.advance()) is not None:
        results.append(result)
    return results


def test_holds_until_the_lookahead_margin_exists() -> None:
    cursor = _cursor(lookahead=1)
    cursor.push_snapshot(_snapshot(0))
    cursor.push_snapshot(_snapshot(5))
    # Segment 0..5 exists, but no lookahead snapshot beyond it yet.
    assert _run(_drain(cursor)) == []


def test_walks_every_frame_index_up_to_the_newest_interpolatable_snapshot() -> None:
    cursor = _cursor(lookahead=1)
    for frame_index in (0, 5, 10):
        cursor.push_snapshot(_snapshot(frame_index))

    results = _run(_drain(cursor))

    # Usable up to snapshot 5 (snapshot 10 is the lookahead margin).
    assert [result.frame_index for result in results] == [0, 1, 2, 3, 4, 5]
    assert [result.is_exact for result in results] == [
        True, False, False, False, False, True,
    ]
    assert all(result.bracket is not None for result in results)


def test_never_returns_the_same_frame_index_twice() -> None:
    cursor = _cursor(lookahead=1)
    for frame_index in (0, 5, 10):
        cursor.push_snapshot(_snapshot(frame_index))

    first = _run(_drain(cursor))
    # Stalled: repeated calls keep returning None, no duplicates.
    assert _run(_drain(cursor)) == []
    assert len({result.frame_index for result in first}) == len(first)


def test_catches_up_when_snapshots_resume_after_a_stall() -> None:
    cursor = _cursor(lookahead=1)
    for frame_index in (0, 5, 10):
        cursor.push_snapshot(_snapshot(frame_index))
    assert [result.frame_index for result in _run(_drain(cursor))] == [0, 1, 2, 3, 4, 5]

    # A long detection gap, then a burst of new snapshots.
    for frame_index in (30, 35):
        cursor.push_snapshot(_snapshot(frame_index))

    resumed = _run(_drain(cursor))
    assert [result.frame_index for result in resumed] == list(range(6, 31))


def test_finish_flushes_through_the_last_snapshot() -> None:
    cursor = _cursor(lookahead=1)
    for frame_index in (0, 5, 10):
        cursor.push_snapshot(_snapshot(frame_index))
    assert [result.frame_index for result in _run(_drain(cursor))] == [0, 1, 2, 3, 4, 5]

    cursor.finish()

    flushed = _run(_drain(cursor))
    assert [result.frame_index for result in flushed] == [6, 7, 8, 9, 10]
    assert flushed[-1].is_exact is True
    # Beyond the last snapshot the cursor has nothing to interpolate — the
    # caller flushes the tail itself.
    assert _run(_drain(cursor)) == []


def test_finish_with_a_single_snapshot_emits_that_frame_held() -> None:
    cursor = _cursor(lookahead=3)
    cursor.push_snapshot(_snapshot(3))
    assert _run(_drain(cursor)) == []

    cursor.finish()

    results = _run(_drain(cursor))
    assert [result.frame_index for result in results] == [3]
    assert results[0].is_exact is True
    assert results[0].bracket is None
    assert [face.track_id for face in results[0].faces] == [0]


def test_prunes_snapshots_behind_the_spline_window() -> None:
    cursor = _cursor(lookahead=1)
    for frame_index in range(0, 200, 5):
        cursor.push_snapshot(_snapshot(frame_index))

    results = _run(_drain(cursor))

    assert [result.frame_index for result in results] == list(range(0, 191))
    # Only the spline window's worth of snapshots may be retained.
    assert len(cursor._scenes[0]) <= 2 * 1 + 2  # noqa: SLF001 - pinning the prune


def _scene_start_snapshot(frame_index: int, x: float) -> kernel.Snapshot[TrackedFace]:
    return kernel.Snapshot(
        frame_index=frame_index,
        faces=[_tracked_face(x, track_id=100)],
        is_scene_start=True,
    )


def test_scene_cut_holds_the_gap_and_restarts_the_window() -> None:
    cursor = _cursor(lookahead=1)
    # Old scene: snapshots at 0, 5, 10. Cut at frame 14; new scene's
    # snapshots at 14, 18, 22 carry a different track id.
    for frame_index in (0, 5, 10):
        cursor.push_snapshot(_snapshot(frame_index))
    cursor.push_snapshot(_scene_start_snapshot(14, x=500.0))
    cursor.push_snapshot(
        kernel.Snapshot(frame_index=18, faces=[_tracked_face(500.0, track_id=100)])
    )
    cursor.push_snapshot(
        kernel.Snapshot(frame_index=22, faces=[_tracked_face(500.0, track_id=100)])
    )

    results = _run(_drain(cursor))

    by_index = {result.frame_index: result for result in results}
    # The old scene runs to its last snapshot (relaxed — it's closed), the
    # gap 11..13 is held with the old scene's final faces, and the new scene
    # renders from 14 up to its own lookahead margin (snapshot 18).
    assert [result.frame_index for result in results] == list(range(0, 19))
    for gap_index in (11, 12, 13):
        assert by_index[gap_index].bracket is None
        assert [face.track_id for face in by_index[gap_index].faces] == [0]
    for new_scene_index in range(14, 19):
        assert {face.track_id for face in by_index[new_scene_index].faces} == {100}
    # No interpolation bracket ever spans the cut.
    for result in results:
        if result.bracket is not None:
            segment_start, segment_end = result.bracket
            assert (segment_start.frame_index < 14) == (segment_end.frame_index < 14)


def test_scene_with_a_single_snapshot_before_the_next_cut() -> None:
    cursor = _cursor(lookahead=1)
    cursor.push_snapshot(_snapshot(0))
    # Scene 2 gets exactly one snapshot before scene 3 begins.
    cursor.push_snapshot(_scene_start_snapshot(4, x=500.0))
    cursor.push_snapshot(_scene_start_snapshot(8, x=900.0))
    cursor.finish()

    results = _run(_drain(cursor))

    assert [result.frame_index for result in results] == list(range(0, 9))
    by_index = {result.frame_index: result for result in results}
    assert by_index[4].is_exact is True
    assert by_index[4].bracket is None
    # Frames 5..7 hold scene 2's only snapshot; 8 is scene 3's.
    for held_index in (5, 6, 7):
        assert by_index[held_index].faces[0].face.detection.bounding_box.x == 500.0
    assert by_index[8].faces[0].face.detection.bounding_box.x == 900.0


def test_track_seen_once_in_window_is_held_not_dropped() -> None:
    cursor = _cursor(lookahead=1)
    # Track 1 appears only in the middle snapshot.
    lonely = _tracked_face(100.0, track_id=1)
    cursor.push_snapshot(_snapshot(0))
    cursor.push_snapshot(
        kernel.Snapshot(frame_index=5, faces=[_tracked_face(5.0), lonely])
    )
    cursor.push_snapshot(_snapshot(10))
    cursor.push_snapshot(_snapshot(15))

    results = _run(_drain(cursor))

    by_index = {result.frame_index: result for result in results}
    # Once the segment starting at 5 is active, track 1 is emitted at its only
    # known position instead of vanishing or being extrapolated.
    faces_at_7 = {face.track_id: face for face in by_index[7].faces}
    assert faces_at_7[1].face.detection.bounding_box.x == 100.0

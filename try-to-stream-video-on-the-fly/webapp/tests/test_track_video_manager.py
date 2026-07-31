"""TrackVideoManager: crop math, per-track stream lifecycle, and new-track pub/sub.

`core.FfmpegFrameSink.start` is monkeypatched to a lightweight fake so this stays a pure-Python
unit test (no real ffmpeg subprocess) — mirrors test_pipeline_manager.py's fake-based style.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import numpy as np

from video_analyzer import core, kernel
from video_analyzer.webapp.track_video_manager import TrackVideoManager, _crop_padded_square


class _FakeFrameSink:
    def __init__(self) -> None:
        self.written_frames: list = []

    async def write_frame(self, annotated_frame: kernel.AnnotatedFrame) -> None:
        self.written_frames.append(annotated_frame.frame.content)

    async def close_stdin(self) -> None:
        pass

    async def read_output_chunk(self, size: int = 65536) -> bytes:
        await asyncio.Event().wait()  # never produces; the pump task just parks until cancelled
        return b""


@asynccontextmanager
async def _fake_frame_sink_cm(*_args: object, **_kwargs: object) -> AsyncIterator[_FakeFrameSink]:
    yield _FakeFrameSink()


def _tracked_face(track_id: str, bounding_box: kernel.BoundingBox) -> kernel.TrackedFace:
    detection = kernel.Detection(
        bounding_box=bounding_box,
        landmarks=kernel.FaceLandmarks(
            left_eye=(0, 0),
            right_eye=(0, 0),
            nose=(0, 0),
            mouth_left=(0, 0),
            mouth_right=(0, 0),
        ),
        confidence=1.0,
    )
    return kernel.TrackedFace(
        track_id=track_id, face=kernel.Face(detection=detection, embedding=None)
    )


def _annotated_frame(content, faces: list[kernel.TrackedFace]) -> kernel.AnnotatedFrame:
    return kernel.AnnotatedFrame(
        frame=kernel.Frame(index=0, content=content),
        faces=faces,
        interpolation_bracket=None,
        is_exact=True,
    )


def _start_manager(monkeypatch):
    monkeypatch.setattr(core.FfmpegFrameSink, "start", _fake_frame_sink_cm)
    return TrackVideoManager.start(
        30.0, sink_config=core.FfmpegFrameSinkConfig(), max_fragments=15
    )


def test_crop_padded_square_centers_and_pads() -> None:
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    box = kernel.BoundingBox(x=90, y=90, width=20, height=20)
    crop = _crop_padded_square(frame, box)
    assert crop is not None
    assert crop.shape == (320, 320, 3)
    assert crop.dtype == np.uint8


def test_crop_padded_square_clamps_at_frame_edge() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    box = kernel.BoundingBox(x=0, y=0, width=10, height=10)
    crop = _crop_padded_square(frame, box)
    assert crop is not None
    assert crop.shape == (320, 320, 3)


def test_crop_padded_square_none_when_box_outside_frame() -> None:
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    box = kernel.BoundingBox(x=1000, y=1000, width=5, height=5)
    assert _crop_padded_square(frame, box) is None


def test_crop_letterboxes_when_edge_clamping_makes_it_non_square() -> None:
    # A narrow frame clamps the padded square's x-axis hard while leaving the y-axis room to
    # spare, so the actual crop region ends up much taller than wide.
    frame = np.full((200, 6, 3), 200, dtype=np.uint8)
    box = kernel.BoundingBox(x=0, y=100, width=4, height=4)
    crop = _crop_padded_square(frame, box)
    assert crop is not None
    assert crop.shape == (320, 320, 3)
    # Preserving the aspect ratio (taller-than-wide source) pillarboxes: black bars on the sides,
    # not squashed content filling the whole square.
    assert np.array_equal(crop[:, 0], np.zeros((320, 3), dtype=np.uint8))
    assert np.array_equal(crop[:, -1], np.zeros((320, 3), dtype=np.uint8))
    assert not np.array_equal(crop[:, 160], np.zeros((320, 3), dtype=np.uint8))


async def test_new_track_starts_stream_and_notifies_subscribers(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        existing, queue = manager.subscribe()
        assert existing == []

        frame = _annotated_frame(
            np.zeros((100, 100, 3), dtype=np.uint8),
            [_tracked_face("5", kernel.BoundingBox(10, 10, 20, 20))],
        )
        await manager.broadcast_frame(frame)

        assert manager.has_track("5")
        assert manager.get_broadcaster("5") is not None
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == "5"


async def test_idle_track_replays_its_buffered_crop_on_a_loop(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        box = kernel.BoundingBox(10, 10, 20, 20)
        live_frame = _annotated_frame(
            np.zeros((100, 100, 3), dtype=np.uint8), [_tracked_face("3", box)]
        )
        await manager.broadcast_frame(live_frame)  # track 3 starts, gets its one real crop

        empty_frame = _annotated_frame(np.zeros((100, 100, 3), dtype=np.uint8), [])
        for _ in range(3):
            await manager.broadcast_frame(empty_frame)  # track 3 absent every tick after

        written = manager._streams["3"].sink.written_frames
        # One live crop, then it keeps getting written every tick even though the track never
        # reappears - the whole point being the browser-side connection never looks stalled.
        assert len(written) == 4
        assert all(frame.shape == (320, 320, 3) for frame in written)
        # Only one crop was ever buffered, so every replay is that exact same crop.
        assert all(np.array_equal(frame, written[0]) for frame in written)


async def test_replay_bounces_back_and_forth_through_buffered_history(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        box = kernel.BoundingBox(10, 10, 20, 20)
        for value in (10, 20, 30, 40):
            live_frame = _annotated_frame(
                np.full((100, 100, 3), value, dtype=np.uint8), [_tracked_face("5", box)]
            )
            await manager.broadcast_frame(live_frame)

        empty_frame = _annotated_frame(np.zeros((100, 100, 3), dtype=np.uint8), [])
        for _ in range(8):
            await manager.broadcast_frame(empty_frame)

        written = manager._streams["5"].sink.written_frames
        assert len(written) == 12  # 4 live crops + 8 replayed
        values = [int(frame.mean()) for frame in written]
        # Live crops of uniformly-colored frames stay uniform after crop+resize, so the mean
        # pixel value alone identifies which buffered crop each write is. Buffer (oldest->newest)
        # is [10, 20, 30, 40]; the idle loop resumes from the frame just before the last live one
        # (30) and ping-pongs both ends forever, rather than jump-cutting back to the oldest.
        assert values == [10, 20, 30, 40, 30, 20, 10, 20, 30, 40, 30, 20]


async def test_subscribe_backfills_tracks_seen_before_it_connected(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        frame = _annotated_frame(
            np.zeros((100, 100, 3), dtype=np.uint8),
            [
                _tracked_face("1", kernel.BoundingBox(10, 10, 20, 20)),
                _tracked_face("2", kernel.BoundingBox(50, 50, 20, 20)),
            ],
        )
        await manager.broadcast_frame(frame)

        existing, _queue = manager.subscribe()
        assert existing == ["1", "2"]


async def test_unknown_track_has_no_broadcaster(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        assert not manager.has_track("99")
        assert manager.get_broadcaster("99") is None


async def test_unsubscribe_stops_further_notifications(monkeypatch) -> None:
    async with _start_manager(monkeypatch) as manager:
        _existing, queue = manager.subscribe()
        manager.unsubscribe(queue)

        frame = _annotated_frame(
            np.zeros((100, 100, 3), dtype=np.uint8),
            [_tracked_face("7", kernel.BoundingBox(10, 10, 20, 20))],
        )
        await manager.broadcast_frame(frame)

        assert queue.empty()


async def test_teardown_sends_none_sentinel_to_subscribers(monkeypatch) -> None:
    manager_cm = _start_manager(monkeypatch)
    async with manager_cm as manager:
        _existing, queue = manager.subscribe()
    # Context exited: the manager tore down and should have woken every subscriber.
    assert await asyncio.wait_for(queue.get(), timeout=1.0) is None

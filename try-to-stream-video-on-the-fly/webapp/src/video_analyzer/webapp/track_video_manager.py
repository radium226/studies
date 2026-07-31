"""Per-track face-loop video: gives every ByteTrack id its own live fMP4 stream, generated on the
fly exactly like the main stream — a dedicated `core.FfmpegFrameSink` + `Broadcaster` per track,
fed a padded-square, letterboxed crop of that track's face on every rendered frame it appears in.
Once a track stops appearing (occluded, out of frame, lost by the tracker), its stream doesn't
stall — it bounces back and forth through its own recent crop history (oldest<->newest, forever)
at the same cadence, until (if ever) the track appears again. The ping-pong keeps it fluid: every
step, including at either end, differs by exactly one buffered frame, so it never jump-cuts.

Implements `kernel.FrameBroadcaster` (the metadata twin of `FrameSink` — see kernel/CLAUDE.md)
rather than wrapping `FrameSink`: it needs the same pristine `AnnotatedFrame` `OverlayFrameSink`
gets, but produces second, unrelated byte streams instead of drawing on the primary one.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import core, kernel

from .broadcaster import Broadcaster
from .iso_bmff import pump_fragments

# Not configuration: presentation choices for the face-loop column, not pipeline tuning knobs —
# same reasoning overlay_frame_sink.py's _BOX_COLOR/_LABEL_FONT get for staying constants.
_THUMBNAIL_SIZE = 320
_CROP_PADDING_FACTOR = 1.6
# How many of a track's most recent crops its idle loop cycles through once it stops appearing —
# a recent-history window, not the track's entire lifetime (which would make the loop grow
# unboundedly long, and the buffer's memory with it, for a track alive for minutes).
_LOOP_BUFFER_FRAMES = 150


class _TrackStream:
    def __init__(self, sink: core.FfmpegFrameSink, broadcaster: Broadcaster) -> None:
        self.sink = sink
        self.broadcaster = broadcaster
        self.buffer: deque[NDArray[np.uint8]] = deque(maxlen=_LOOP_BUFFER_FRAMES)
        self._replay_pos = 0
        self._replay_dir = -1

    def record_live_crop(self, crop: NDArray[np.uint8]) -> None:
        """A fresh crop landed: buffer it, and arm the idle loop to bounce backward from here
        the next time this track goes quiet. The previous buffered frame is already adjacent in
        time to this one, so resuming there (rather than jumping to the oldest buffered frame)
        means the loop never has to jump-cut, even on the very first idle tick."""
        self.buffer.append(crop)
        self._replay_pos = len(self.buffer) - 1
        self._replay_dir = -1

    def next_replay_frame(self) -> NDArray[np.uint8]:
        """Advance one step of a ping-pong bounce through the buffer (oldest<->newest, forever)
        and return that frame. Every step — including the ones at either end — differs from the
        last by exactly one buffered frame, so the loop is fluid: it reverses direction instead
        of ever teleporting from one end of the buffer back to the other."""
        if len(self.buffer) > 1:
            pos = self._replay_pos + self._replay_dir
            if pos <= 0:
                pos, self._replay_dir = 0, 1
            elif pos >= len(self.buffer) - 1:
                pos, self._replay_dir = len(self.buffer) - 1, -1
            self._replay_pos = pos
        return self.buffer[self._replay_pos]


def _letterbox_resize(image: NDArray[np.uint8], size: int) -> NDArray[np.uint8]:
    """Scale `image` to fit within a `size`x`size` canvas without distorting its aspect ratio,
    centering it and filling the remainder with black bars. A no-op when `image` is already
    square (the common case): it fills the canvas exactly, no bars."""
    height, width = image.shape[:2]
    scale = size / max(height, width)
    scaled_width = max(1, round(width * scale))
    scaled_height = max(1, round(height * scale))
    resized = cv2.resize(image, (scaled_width, scaled_height)).astype(np.uint8)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    x_offset = (size - scaled_width) // 2
    y_offset = (size - scaled_height) // 2
    canvas[y_offset : y_offset + scaled_height, x_offset : x_offset + scaled_width] = resized
    return canvas


def _crop_padded_square(
    frame: NDArray[np.uint8], bounding_box: kernel.BoundingBox
) -> NDArray[np.uint8] | None:
    """Crop a square around `bounding_box`, padded by `_CROP_PADDING_FACTOR` and clamped to the
    frame's bounds — which, near an edge, can make the actual crop region non-square again (the
    square target gets clipped on one axis but not the other). Letterboxed into `_THUMBNAIL_SIZE`
    either way, so a face near the frame edge never comes out visibly stretched. None if the box
    lies entirely outside the frame (clamping collapses the region to empty)."""
    height, width = frame.shape[:2]
    center_x = bounding_box.x + bounding_box.width / 2
    center_y = bounding_box.y + bounding_box.height / 2
    half_side = max(bounding_box.width, bounding_box.height) * _CROP_PADDING_FACTOR / 2
    x0 = int(max(0, center_x - half_side))
    y0 = int(max(0, center_y - half_side))
    x1 = int(min(width, center_x + half_side))
    y1 = int(min(height, center_y + half_side))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = frame[y0:y1, x0:x1]
    return _letterbox_resize(crop, _THUMBNAIL_SIZE)


class TrackVideoManager(
    kernel.FrameBroadcaster[NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]]
):
    def __init__(
        self, fps: float, *, sink_config: core.FfmpegFrameSinkConfig, max_fragments: int
    ) -> None:
        self._fps = fps
        self._sink_config = sink_config
        self._max_fragments = max_fragments
        self._stack = AsyncExitStack()
        self._streams: dict[str, _TrackStream] = {}
        self._track_order: list[str] = []
        self._pump_tasks: set[asyncio.Task[None]] = set()
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._next_frame_index = 0

    @classmethod
    @asynccontextmanager
    async def start(
        cls, fps: float, *, sink_config: core.FfmpegFrameSinkConfig, max_fragments: int
    ) -> AsyncIterator[Self]:
        self = cls(fps, sink_config=sink_config, max_fragments=max_fragments)
        try:
            yield self
        finally:
            # Unblock any websocket route parked on a subscriber queue before the underlying
            # streams disappear out from under it — same "wake everyone up" reasoning
            # pump_fragments applies to a closing Broadcaster.
            for queue in self._subscribers:
                queue.put_nowait(None)
            # Cancel the pump tasks before the exit stack tears down their sinks/broadcasters —
            # same ordering Orchestrator.start uses, so a task never reads from a process that's
            # mid-shutdown.
            tasks = list(self._pump_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._stack.aclose()

    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        faces_by_track = {face.track_id: face for face in annotated_frame.faces}
        for track_id in faces_by_track:
            if track_id not in self._streams:
                await self._start_track(track_id)

        # Every track with a stream gets exactly one frame written this tick — a fresh crop if
        # it's actually present, otherwise the next step of a ping-pong bounce through its own
        # recent history. Writing every tick regardless keeps each per-track stream's byte rate
        # steady forever, so a quiet track's connection never looks stalled to the browser
        # (attachLiveStream's own idle timeout) — it just keeps bouncing.
        for track_id, stream in self._streams.items():
            crop = None
            tracked_face = faces_by_track.get(track_id)
            if tracked_face is not None:
                crop = _crop_padded_square(
                    annotated_frame.frame.content, tracked_face.face.detection.bounding_box
                )
            if crop is not None:
                stream.record_live_crop(crop)
            elif stream.buffer:
                crop = stream.next_replay_frame()
            else:
                continue  # started this very tick but the first crop clipped to nothing
            await self._write(stream, crop)

    async def _write(self, stream: _TrackStream, crop: NDArray[np.uint8]) -> None:
        frame_index = self._next_frame_index
        self._next_frame_index += 1
        await stream.sink.write_frame(
            kernel.AnnotatedFrame(
                frame=kernel.Frame(index=frame_index, content=crop),
                faces=[],
                interpolation_bracket=None,
                is_exact=True,
            )
        )

    async def _start_track(self, track_id: str) -> None:
        sink = await self._stack.enter_async_context(
            core.FfmpegFrameSink.start(
                _THUMBNAIL_SIZE, _THUMBNAIL_SIZE, self._fps, config=self._sink_config
            )
        )
        broadcaster = await self._stack.enter_async_context(
            Broadcaster.start(max_fragments=self._max_fragments)
        )
        self._streams[track_id] = _TrackStream(sink, broadcaster)
        self._track_order.append(track_id)
        task = asyncio.create_task(
            pump_fragments(sink.read_output_chunk, broadcaster), name=f"track-pump-{track_id}"
        )
        self._pump_tasks.add(task)
        task.add_done_callback(self._pump_tasks.discard)
        for queue in self._subscribers:
            queue.put_nowait(track_id)

    def has_track(self, track_id: str) -> bool:
        return track_id in self._streams

    def get_broadcaster(self, track_id: str) -> Broadcaster | None:
        stream = self._streams.get(track_id)
        return stream.broadcaster if stream is not None else None

    def subscribe(self) -> tuple[list[str], asyncio.Queue[str | None]]:
        """Snapshot of every track id seen so far, plus a queue that receives every subsequent
        one live (and a final `None` sentinel when this manager is torn down)."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._subscribers.add(queue)
        return list(self._track_order), queue

    def unsubscribe(self, queue: asyncio.Queue[str | None]) -> None:
        self._subscribers.discard(queue)

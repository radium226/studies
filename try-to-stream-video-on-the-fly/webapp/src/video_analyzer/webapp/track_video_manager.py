"""Per-track face-loop video: gives every ByteTrack id its own live fMP4 stream, generated on the
fly exactly like the main stream — a dedicated `core.FfmpegFrameSink` + `Broadcaster` per track,
fed a padded-square crop of that track's face on every rendered frame it appears in.

Implements `kernel.FrameBroadcaster` (the metadata twin of `FrameSink` — see kernel/CLAUDE.md)
rather than wrapping `FrameSink`: it needs the same pristine `AnnotatedFrame` `OverlayFrameSink`
gets, but produces second, unrelated byte streams instead of drawing on the primary one.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import core, kernel

from .broadcaster import Broadcaster
from .iso_bmff import pump_fragments

# Not configuration: a presentation choice for the face-loop column, not a pipeline tuning knob —
# same reasoning overlay_frame_sink.py's _BOX_COLOR/_LABEL_FONT get for staying constants.
_THUMBNAIL_SIZE = 320
_CROP_PADDING_FACTOR = 1.6


def _crop_padded_square(
    frame: NDArray[np.uint8], bounding_box: kernel.BoundingBox
) -> NDArray[np.uint8] | None:
    """Crop a square around `bounding_box`, padded by `_CROP_PADDING_FACTOR`, clamped to the
    frame's bounds, and resized to `_THUMBNAIL_SIZE`. None if the box lies entirely outside the
    frame (clamping collapses the region to empty)."""
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
    return cv2.resize(crop, (_THUMBNAIL_SIZE, _THUMBNAIL_SIZE)).astype(np.uint8)


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
        self._sinks: dict[int, core.FfmpegFrameSink] = {}
        self._broadcasters: dict[int, Broadcaster] = {}
        self._track_order: list[int] = []
        self._pump_tasks: set[asyncio.Task[None]] = set()
        self._subscribers: set[asyncio.Queue[int | None]] = set()
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
        for tracked_face in annotated_frame.faces:
            crop = _crop_padded_square(
                annotated_frame.frame.content, tracked_face.face.detection.bounding_box
            )
            if crop is None:
                continue
            sink = self._sinks.get(tracked_face.track_id)
            if sink is None:
                sink = await self._start_track(tracked_face.track_id)
            frame_index = self._next_frame_index
            self._next_frame_index += 1
            await sink.write_frame(
                kernel.AnnotatedFrame(
                    frame=kernel.Frame(index=frame_index, content=crop),
                    faces=[],
                    interpolation_bracket=None,
                    is_exact=True,
                )
            )

    async def _start_track(self, track_id: int) -> core.FfmpegFrameSink:
        sink = await self._stack.enter_async_context(
            core.FfmpegFrameSink.start(
                _THUMBNAIL_SIZE, _THUMBNAIL_SIZE, self._fps, config=self._sink_config
            )
        )
        broadcaster = await self._stack.enter_async_context(
            Broadcaster.start(max_fragments=self._max_fragments)
        )
        self._sinks[track_id] = sink
        self._broadcasters[track_id] = broadcaster
        self._track_order.append(track_id)
        task = asyncio.create_task(
            pump_fragments(sink.read_output_chunk, broadcaster), name=f"track-pump-{track_id}"
        )
        self._pump_tasks.add(task)
        task.add_done_callback(self._pump_tasks.discard)
        for queue in self._subscribers:
            queue.put_nowait(track_id)
        return sink

    def has_track(self, track_id: int) -> bool:
        return track_id in self._broadcasters

    def get_broadcaster(self, track_id: int) -> Broadcaster | None:
        return self._broadcasters.get(track_id)

    def subscribe(self) -> tuple[list[int], asyncio.Queue[int | None]]:
        """Snapshot of every track id seen so far, plus a queue that receives every subsequent
        one live (and a final `None` sentinel when this manager is torn down)."""
        queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._subscribers.add(queue)
        return list(self._track_order), queue

    def unsubscribe(self, queue: asyncio.Queue[int | None]) -> None:
        self._subscribers.discard(queue)

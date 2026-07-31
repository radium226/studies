"""Wraps `core.FfmpegFrameSink` to draw detections before encoding.

`core.FfmpegFrameSink` deliberately draws nothing (see its own module docstring) — it only knows
about ffmpeg's raw input, not detections. `cli`'s `FfplayFrameSink` draws inline because it *is*
the sink; this package's sink is `core`'s ffmpeg encoder, so the drawing step lives here instead,
as a thin decorator around it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import core, kernel

_BOX_COLOR = (0, 255, 0)
_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _draw_detections(
    frame: NDArray[np.uint8],
    detections: list[kernel.TrackedFace[NDArray[np.float32]]],
    is_exact: bool,
) -> None:
    """Solid box = landed on a real detection, dashed = interpolated — same convention `core`'s
    own `overlay.py` documents, and the same drawing `cli/ffplay_frame_sink.py` does inline."""
    for tracked_face in detections:
        bounding_box = tracked_face.face.detection.bounding_box
        pt1 = (int(bounding_box.x), int(bounding_box.y))
        pt2 = (int(bounding_box.x + bounding_box.width), int(bounding_box.y + bounding_box.height))
        if is_exact:
            cv2.rectangle(frame, pt1, pt2, _BOX_COLOR, 2)
        else:
            core.draw_dashed_rect(frame, pt1, pt2, _BOX_COLOR)
        cv2.putText(
            frame,
            f"#{tracked_face.track_id}",
            (pt1[0], max(0, pt1[1] - 8)),
            _LABEL_FONT,
            0.5,
            _BOX_COLOR,
            1,
            cv2.LINE_AA,
        )


class OverlayFrameSink(
    kernel.FrameSink[NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]]
):
    def __init__(self, inner: core.FfmpegFrameSink) -> None:
        self._inner = inner

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        width: int,
        height: int,
        fps: float,
        *,
        config: core.FfmpegFrameSinkConfig | None = None,
    ) -> AsyncIterator[Self]:
        async with core.FfmpegFrameSink.start(width, height, fps, config=config) as inner:
            yield cls(inner)

    async def write_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        # Copy before drawing: the pipeline hands this same Frame object to the detection
        # buffer and to the FrameBroadcaster right after this call — see kernel.FrameSink's
        # docstring. FfmpegFrameSource frames are also read-only views over the decoder pipe's
        # bytes, so cv2 would reject drawing in place outright anyway.
        drawn = annotated_frame.frame.content.copy()
        _draw_detections(drawn, annotated_frame.faces, annotated_frame.is_exact)
        await self._inner.write_frame(
            replace(annotated_frame, frame=replace(annotated_frame.frame, content=drawn))
        )

    async def close_stdin(self) -> None:
        await self._inner.close_stdin()

    async def read_output_chunk(self, size: int = 65536) -> bytes:
        return await self._inner.read_output_chunk(size)

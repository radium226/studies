"""Wraps `core.FfmpegFrameSink` to draw detections before encoding.

`core.FfmpegFrameSink` deliberately draws nothing (see its own module docstring) — it only knows
about ffmpeg's raw input, not detections. `cli`'s `FfplayFrameSink` draws inline because it *is*
the sink; this package's sink is `core`'s ffmpeg encoder, so the drawing step lives here instead,
as a thin decorator around it.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import core, kernel

_FALLBACK_COLOR = (180, 180, 180)
_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_LANDMARK_RADIUS = 3


def _track_color(embedding: NDArray[np.float32]) -> tuple[int, int, int]:
    """A stable, vivid colour per face identity, derived from its ArcFace embedding.

    `app/engine.py` did this by mapping the first three embedding dimensions straight onto BGR
    (`(v + 1) / 2 * 255`). That reads well but doesn't actually work: ArcFace embeddings are
    L2-normalized over 512 dimensions, so a single component averages about `1/sqrt(512)` ~= 0.04
    and the formula collapses every identity into near-identical mid-grey. We take the *angle*
    between the first two dimensions instead and use it as a hue at full saturation and value,
    which spreads identities across the whole colour wheel while staying just as deterministic.

    The embedding is carried unchanged through interpolation (`TrackedFace.with_vector` copies it
    from the template), so a track keeps one colour for its whole life."""
    if embedding is None or len(embedding) < 2:
        return _FALLBACK_COLOR
    angle = math.atan2(float(embedding[1]), float(embedding[0]))
    # OpenCV packs hue into [0, 180) for 8-bit HSV, not [0, 360).
    hue = int((angle + math.pi) / (2.0 * math.pi) * 180.0) % 180
    blue, green, red = cv2.cvtColor(
        np.array([[[hue, 255, 255]]], dtype=np.uint8), cv2.COLOR_HSV2BGR
    )[0][0]
    return (int(blue), int(green), int(red))


def _draw_detections(
    frame: NDArray[np.uint8],
    detections: list[kernel.TrackedFace[NDArray[np.float32]]],
    is_exact: bool,
) -> None:
    """Solid box = landed on a real detection, dashed = interpolated — same convention `core`'s
    own `overlay.py` documents, and the same drawing `cli/ffplay_frame_sink.py` does inline.

    Box, landmarks and label share one per-identity colour, so a viewer can follow a face across
    the frame without reading its id."""
    for tracked_face in detections:
        detection = tracked_face.face.detection
        color = _track_color(tracked_face.face.embedding)
        bounding_box = detection.bounding_box
        pt1 = (int(bounding_box.x), int(bounding_box.y))
        pt2 = (int(bounding_box.x + bounding_box.width), int(bounding_box.y + bounding_box.height))
        if is_exact:
            cv2.rectangle(frame, pt1, pt2, color, 2)
        else:
            core.draw_dashed_rect(frame, pt1, pt2, color)
        # The 5 SCRFD keypoints ride through the whole pipeline (kernel.TrackedFace interpolates
        # them alongside the box, as 14 floats), so drawing them costs nothing extra and makes
        # a bad interpolation obvious — the points drift off the face long before the box does.
        landmarks = detection.landmarks
        for x, y in (
            landmarks.left_eye,
            landmarks.right_eye,
            landmarks.nose,
            landmarks.mouth_left,
            landmarks.mouth_right,
        ):
            cv2.circle(frame, (int(x), int(y)), _LANDMARK_RADIUS, color, -1)
        cv2.putText(
            frame,
            f"#{tracked_face.track_id}",
            (pt1[0], max(0, pt1[1] - 8)),
            _LABEL_FONT,
            0.5,
            color,
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

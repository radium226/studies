"""Pipe annotated BGR24 frames into an `ffplay` subprocess for on-screen playback.

Draws each frame's detections in-place before writing it (solid box = landed on a real
detection, dashed = interpolated — same convention `core`'s own `overlay.py` documents), then
writes the raw bytes straight to `ffplay`'s stdin. `ffplay` decodes nothing here — it's just
handed a rawvideo stream and shows it in a window, so no encoder process is involved.

This sink is CLI-owned rather than living in `core`: `core.FrameBroadcaster` and any playback
transport are explicitly not `core`'s concern (see `core/CLAUDE.md`) — an application wiring the
pipeline together decides where the bytes go, and here that's `ffplay`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Self

import cv2
import numpy as np
from loguru import logger
from numpy.typing import NDArray
from video_analyzer.core import overlay

from video_analyzer import kernel

_BOX_COLOR = (0, 255, 0)
_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _draw_detections(
    frame: NDArray[np.uint8],
    detections: list[kernel.TrackedFace[NDArray[np.float32]]],
    is_exact: bool,
) -> None:
    for tracked_face in detections:
        bounding_box = tracked_face.face.detection.bounding_box
        pt1 = (int(bounding_box.x), int(bounding_box.y))
        pt2 = (int(bounding_box.x + bounding_box.width), int(bounding_box.y + bounding_box.height))
        if is_exact:
            cv2.rectangle(frame, pt1, pt2, _BOX_COLOR, 2)
        else:
            overlay.draw_dashed_rect(frame, pt1, pt2, _BOX_COLOR)
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


class FfplayFrameSink(
    kernel.FrameSink[NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]]
):
    def __init__(self, width: int, height: int, fps: float) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        width: int,
        height: int,
        fps: float,
        *,
        stop_timeout: float = 5.0,
    ) -> AsyncIterator[Self]:
        self = cls(width, height, fps)
        self._proc = await asyncio.create_subprocess_exec(
            *self._ffplay_cmd(),
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc.stderr))
        try:
            yield self
        finally:
            await self._shutdown(stop_timeout)

    async def write_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        frame = annotated_frame.frame.content
        _draw_detections(frame, annotated_frame.detections, annotated_frame.is_exact)
        try:
            self._proc.stdin.write(frame.tobytes())
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # ffplay window was closed by the user — let the source drain out.
            pass

    async def _shutdown(self, timeout: float) -> None:
        assert self._proc is not None and self._stderr_task is not None
        if self._proc.stdin is not None and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await self._proc.stdin.wait_closed()
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()
        try:
            async with asyncio.timeout(timeout):
                await self._proc.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
            await self._proc.wait()
        self._stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._stderr_task

    @staticmethod
    async def _drain_stderr(stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            logger.info("ffplay: {}", line.decode(errors="replace").rstrip())

    def _ffplay_cmd(self) -> list[str]:
        return [
            "ffplay",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-autoexit",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgr24",
            "-video_size",
            f"{self._width}x{self._height}",
            "-framerate",
            str(self._fps),
            "-i",
            "-",
        ]

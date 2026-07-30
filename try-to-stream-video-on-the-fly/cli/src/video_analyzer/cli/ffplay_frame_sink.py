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
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray
from video_analyzer.core import overlay, pipe_io

from video_analyzer import kernel

from .config import FfplayFrameSinkConfig

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
    def __init__(
        self,
        width: int,
        height: int,
        fps: float,
        *,
        config: FfplayFrameSinkConfig | None = None,
    ) -> None:
        # Width/height/fps describe the raw byte stream on ffplay's stdin —
        # they must match the frames actually written, so they are arguments,
        # not config.
        self._width = width
        self._height = height
        self._fps = fps
        self.config = config if config is not None else FfplayFrameSinkConfig()
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
        config: FfplayFrameSinkConfig | None = None,
    ) -> AsyncIterator[Self]:
        self = cls(width, height, fps, config=config)
        self._proc = await asyncio.create_subprocess_exec(
            *self._ffplay_cmd(),
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._ffplay_env(),
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(
            pipe_io.drain_stderr(self._proc.stderr, "ffplay")
        )
        try:
            yield self
        finally:
            await self._shutdown(self.config.stop_timeout)

    async def write_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        # Single copy per emitted frame, matching app/'s own
        # `pending.frame.copy()`. Drawing into `frame.content` directly is not
        # ours to do: the pipeline hands the very same `Frame` object to the
        # detection buffer and to the broadcaster, so burned-in boxes would leak
        # into both — and `FfmpegFrameSource` frames are read-only views over the
        # decoder pipe's `bytes`, so OpenCV rejects them outright anyway.
        frame = annotated_frame.frame.content.copy()
        _draw_detections(frame, annotated_frame.faces, annotated_frame.is_exact)
        await self.write_raw_frame(frame)

    async def write_raw_frame(self, content: NDArray[np.uint8]) -> None:
        """Show one frame exactly as given — no detection overlay. For callers
        with plain pixels and no `AnnotatedFrame` (e.g. `--play-tracks` crop
        playback)."""
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(content.tobytes())
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # ffplay window was closed by the user — let the source drain out.
            pass

    async def _shutdown(self, timeout: float) -> None:
        assert self._proc is not None and self._stderr_task is not None
        # Close stdin first so ffplay's -autoexit can end the process
        # normally; only then terminate whatever is left. (core's
        # shutdown_process isn't reusable as-is: it drains a stdout pipe this
        # process doesn't have.)
        if self._proc.stdin is not None and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await self._proc.stdin.wait_closed()
        await pipe_io.terminate_and_wait(self._proc, timeout)
        self._stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._stderr_task

    @staticmethod
    def _ffplay_env() -> dict[str, str] | None:
        """Ask SDL for its native Wayland driver when we're on a Wayland session.

        Left to itself, SDL picks its x11 driver and goes through XWayland, whose
        blit path is far too slow for a full-size rawvideo stream: a 720x1280
        clip measured ~6 fps that way versus exact realtime on the Wayland
        driver. Since this sink pushes uncompressed frames at video rate, that
        difference is the difference between smooth playback and the pipeline
        spending the whole run under backpressure.

        Only a default — an explicit `SDL_VIDEODRIVER` always wins. Returns None
        to inherit the environment unchanged.
        """
        if "WAYLAND_DISPLAY" not in os.environ or "SDL_VIDEODRIVER" in os.environ:
            return None
        return os.environ | {"SDL_VIDEODRIVER": "wayland"}

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

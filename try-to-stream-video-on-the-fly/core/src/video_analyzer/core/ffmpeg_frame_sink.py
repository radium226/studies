"""Wrap the ffmpeg encoder subprocess as a `kernel.FrameSink`: accepts
annotated BGR24 `(H, W, 3)` frames, writes the raw bytes to the encoder's
stdin. Overlay drawing is not done here — a caller wanting boxes burned into
the video should draw onto `annotated_frame.frame.content` (using
`draw_caption_text`/`draw_dashed_rect` from this package) before it reaches
this sink; this class only knows about ffmpeg's raw input, not detections.

The encoder's *output* side (the fragmented MP4 byte stream — box parsing,
broadcasting to viewers) is not part of any kernel contract and stays outside
this class; read `read_output_chunk` yourself and wire it up downstream.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Self

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel

from .config import FfmpegFrameSinkConfig
from .pipe_io import drain_stderr, shutdown_process

# Not configuration: a format invariant, and additionally baked into the
# `-force_key_frames` expression below. MSE needs fragment boundaries to land
# on keyframes.
_KEYFRAME_INTERVAL_SECONDS = 2


class FfmpegFrameSink[FaceRecordT](kernel.FrameSink[NDArray[np.uint8], FaceRecordT]):
    def __init__(
        self,
        width: int,
        height: int,
        fps: float,
        *,
        config: FfmpegFrameSinkConfig | None = None,
    ) -> None:
        # Width/height/fps describe the raw byte stream in the pipe — they must
        # match the frames actually written, so they are arguments, not config.
        self._width = width
        self._height = height
        self._fps = fps
        self.config = config if config is not None else FfmpegFrameSinkConfig()
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
        config: FfmpegFrameSinkConfig | None = None,
    ) -> AsyncIterator[Self]:
        self = cls(width, height, fps, config=config)
        self._proc = await asyncio.create_subprocess_exec(
            *self._encoder_cmd(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(drain_stderr(self._proc.stderr, "encoder"))
        try:
            yield self
        finally:
            assert self._proc is not None and self._stderr_task is not None
            await shutdown_process(
                self._proc, self._stderr_task, self.config.stop_timeout
            )

    async def write_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[NDArray[np.uint8], FaceRecordT],
    ) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(annotated_frame.frame.content.tobytes())
        await self._proc.stdin.drain()

    async def close_stdin(self) -> None:
        """Signal end of input: ffmpeg flushes its trailing fragments and then
        EOFs its own stdout, letting a downstream box-parse loop finish naturally."""
        assert self._proc is not None and self._proc.stdin is not None
        stdin = self._proc.stdin
        if stdin.is_closing():
            return
        stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await stdin.wait_closed()

    async def read_output_chunk(self, size: int = 65536) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        return await self._proc.stdout.read(size)

    def _encoder_cmd(self) -> list[str]:
        keyframe_interval_frames = max(1, round(self._fps * _KEYFRAME_INTERVAL_SECONDS))
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-g",
            str(keyframe_interval_frames),
            "-keyint_min",
            str(keyframe_interval_frames),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{_KEYFRAME_INTERVAL_SECONDS})",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration",
            str(self.config.frag_duration_ms * 1000),
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            "pipe:1",
        ]

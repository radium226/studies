"""Wrap the ffmpeg decoder subprocess as a `kernel.FrameSource`: reads a video
source, exposes raw BGR24 `(H, W, 3)` frames one at a time."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel

from ._pipe_io import drain_stderr, read_exact, shutdown_process


@dataclass(frozen=True, slots=True)
class VideoInfo:
    width: int
    height: int
    fps: float


async def probe_video_info(source: str | Path) -> VideoInfo:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-of",
        "json",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed ({proc.returncode}): {stderr.decode(errors='replace')}"
        )
    stream = json.loads(stdout)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return VideoInfo(int(stream["width"]), int(stream["height"]), fps)


def resolve_resize(
    source_width: int, source_height: int, resize: tuple[int, int]
) -> tuple[int, int]:
    """Resolve ffmpeg-style -1 placeholders to actual pixel counts.

    -1 means "keep aspect ratio, round to nearest even number". Always clamps
    the result to even dimensions (even the (-1, -1) "keep native" passthrough)
    since libx264's yuv420p output requires even width/height.
    """
    resized_width, resized_height = resize
    if resized_width == -1 and resized_height == -1:
        resized_width, resized_height = source_width, source_height
    elif resized_width == -1:
        resized_width = max(1, round(source_width * resized_height / source_height))
        resized_width += resized_width % 2
    elif resized_height == -1:
        resized_height = max(1, round(source_height * resized_width / source_width))
        resized_height += resized_height % 2
    resized_width -= resized_width % 2
    resized_height -= resized_height % 2
    return resized_width, resized_height


class FfmpegFrameSource(kernel.FrameSource[NDArray[np.uint8]]):
    def __init__(
        self,
        source: str,
        video_info: VideoInfo,
        *,
        loop: bool = True,
        resize: tuple[int, int] | None = None,
        read_rate: float = 1.0,
    ) -> None:
        self._source = source
        self.video_info = video_info
        self._loop = loop
        self._resize = resize
        self._read_rate = read_rate
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._next_index = 0

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        source: str,
        *,
        loop: bool = True,
        resize: tuple[int, int] | None = None,
        read_rate: float = 1.0,
        stop_timeout: float = 5.0,
    ) -> AsyncIterator[Self]:
        source_video_info = await probe_video_info(source)
        output_width, output_height = resolve_resize(
            source_video_info.width, source_video_info.height, resize or (-1, -1)
        )
        decoder_resize = (
            (output_width, output_height)
            if (output_width, output_height) != (source_video_info.width, source_video_info.height)
            else None
        )

        self = cls(
            source,
            VideoInfo(output_width, output_height, source_video_info.fps),
            loop=loop,
            resize=decoder_resize,
            read_rate=read_rate,
        )
        self._proc = await asyncio.create_subprocess_exec(
            *self._decoder_cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(drain_stderr(self._proc.stderr, "decoder"))
        try:
            yield self
        finally:
            assert self._proc is not None and self._stderr_task is not None
            await shutdown_process(self._proc, self._stderr_task, stop_timeout)

    async def read_frame(self) -> kernel.Frame[NDArray[np.uint8]] | None:
        assert self._proc is not None and self._proc.stdout is not None
        width, height = self.video_info.width, self.video_info.height
        frame_size = width * height * 3
        raw = await read_exact(self._proc.stdout, frame_size)
        if raw is None:
            return None
        content = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
        frame = kernel.Frame(index=self._next_index, content=content)
        self._next_index += 1
        return frame

    def _decoder_cmd(self) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        if self._loop:
            cmd += ["-stream_loop", "-1"]
        # -readrate paces how fast ffmpeg emits decoded frames (every frame is
        # still decoded); -readrate 1 is equivalent to -re.
        cmd += ["-readrate", str(self._read_rate)]
        cmd += ["-i", self._source, "-an"]
        if self._resize:
            cmd += ["-vf", f"scale={self._resize[0]}:{self._resize[1]}"]
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        return cmd

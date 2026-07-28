"""Wrap the ffmpeg decoder subprocess as a `kernel.FrameSource`: reads a video
source, exposes raw BGR24 `(H, W, 3)` frames one at a time."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple, Self

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel

from ._pipe_io import drain_stderr, read_exact, shutdown_process


class VideoInfo(NamedTuple):
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


def resolve_resize(src_w: int, src_h: int, resize: tuple[int, int]) -> tuple[int, int]:
    """Resolve ffmpeg-style -1 placeholders to actual pixel counts.

    -1 means "keep aspect ratio, round to nearest even number". Always clamps
    the result to even dimensions (even the (-1, -1) "keep native" passthrough)
    since libx264's yuv420p output requires even width/height.
    """
    rw, rh = resize
    if rw == -1 and rh == -1:
        rw, rh = src_w, src_h
    elif rw == -1:
        rw = max(1, round(src_w * rh / src_h))
        rw += rw % 2
    elif rh == -1:
        rh = max(1, round(src_h * rw / src_w))
        rh += rh % 2
    rw -= rw % 2
    rh -= rh % 2
    return rw, rh


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
        width, height, fps = await probe_video_info(source)
        out_w, out_h = resolve_resize(width, height, resize or (-1, -1))
        decoder_resize = (out_w, out_h) if (out_w, out_h) != (width, height) else None

        self = cls(
            source,
            VideoInfo(out_w, out_h, fps),
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
        width, height, _ = self.video_info
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

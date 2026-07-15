"""Wrap the ffmpeg decoder subprocess: reads a looped source file, exposes raw
BGR24 frames one at a time.

Knows nothing about the Writer, the frame-processing Engine, or the
Broadcaster - it only spawns the decoder process and hands out raw frame
bytes. The Orchestrator wires it together with the rest of the pipeline.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple

from video_streamer.pipe_io import (
    drain_stderr,
    read_exact,
    shutdown_process,
)


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


class Reader:
    def __init__(
        self,
        source: str,
        *,
        loop: bool = True,
        resize: tuple[int, int] | None = None,
        read_rate: float = 1.0,
    ) -> None:
        self._source = source
        self._loop = loop
        self._resize = resize
        self._read_rate = read_rate
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

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
    ) -> AsyncIterator[Reader]:
        self = cls(source, loop=loop, resize=resize, read_rate=read_rate)
        self._proc = await asyncio.create_subprocess_exec(
            *self._decoder_cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(
            drain_stderr(self._proc.stderr, "decoder")
        )
        try:
            yield self
        finally:
            await self._stop(stop_timeout)

    async def read_frame(self, frame_size: int) -> bytes | None:
        assert self._proc is not None and self._proc.stdout is not None
        return await read_exact(self._proc.stdout, frame_size)

    async def _stop(self, timeout: float) -> None:
        assert self._proc is not None and self._stderr_task is not None
        await shutdown_process(self._proc, self._stderr_task, timeout)

    def _decoder_cmd(self) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        if self._loop:
            cmd += ["-stream_loop", "-1"]
        # -readrate paces how fast ffmpeg emits decoded frames (every frame is
        # still decoded); -readrate 1 is equivalent to -re. speed_factor > 1
        # feeds the pipeline faster than realtime for faster playback.
        cmd += ["-readrate", str(self._read_rate)]
        cmd += ["-i", self._source, "-an"]
        if self._resize:
            cmd += ["-vf", f"scale={self._resize[0]}:{self._resize[1]}"]
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        return cmd

"""Wrap the ffmpeg decoder subprocess: reads a looped source file, exposes raw
BGR24 frames one at a time.

Knows nothing about the Writer, the frame-processing Engine, or the
Broadcaster - it only spawns the decoder process and hands out raw frame
bytes. The Orchestrator wires it together with the rest of the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from video_streamer.pipe_io import (
    drain_and_discard,
    drain_stderr,
    read_exact,
    terminate_and_wait,
)


async def probe_video_info(path: Path) -> tuple[int, int, float]:
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
        str(path),
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
    return int(stream["width"]), int(stream["height"]), fps


class Reader:
    def __init__(self, source_path: Path) -> None:
        self._source_path = source_path
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    @classmethod
    @asynccontextmanager
    async def start(
        cls, source_path: Path, *, stop_timeout: float = 5.0
    ) -> AsyncIterator[Reader]:
        self = cls(source_path)
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
        assert self._proc is not None and self._proc.stdout is not None
        # Keep draining stdout while shutting down - see
        # drain_and_discard's docstring for why this is required for
        # wait() to ever resolve once nobody's reading frames anymore.
        drain_task = asyncio.ensure_future(drain_and_discard(self._proc.stdout))
        await terminate_and_wait(self._proc, timeout)
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task
        assert self._stderr_task is not None
        self._stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._stderr_task

    def _decoder_cmd(self) -> list[str]:
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stream_loop",
            "-1",
            "-re",
            "-i",
            str(self._source_path),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ]

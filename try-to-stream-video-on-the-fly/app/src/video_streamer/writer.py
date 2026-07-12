"""Wrap the ffmpeg encoder subprocess: accepts raw BGR24 frames on stdin,
exposes the fragmented MP4 byte stream it produces on stdout.

Symmetric counterpart to Reader. Knows nothing about ISO BMFF box parsing or
the Broadcaster - the Orchestrator reads its raw output chunks and does that
itself.
"""

from __future__ import annotations

import asyncio
import contextlib

from video_streamer.pipe_io import drain_and_discard, drain_stderr, terminate_and_wait

KEYFRAME_INTERVAL_SECONDS = 2
DEFAULT_FRAG_DURATION_MS = 200


class Writer:
    def __init__(
        self,
        width: int,
        height: int,
        fps: float,
        *,
        frag_duration_ms: int = DEFAULT_FRAG_DURATION_MS,
    ) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._frag_duration_ms = frag_duration_ms
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._encoder_cmd(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(drain_stderr(self._proc.stderr, "encoder"))

    async def write_frame(self, data: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def read_output_chunk(self, size: int = 65536) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        return await self._proc.stdout.read(size)

    async def stop(self, timeout: float = 5.0) -> None:
        if self._proc is not None:
            assert self._proc.stdout is not None
            # See Reader.stop() for why stdout must keep being drained.
            drain_task = asyncio.ensure_future(drain_and_discard(self._proc.stdout))
            await terminate_and_wait(self._proc, timeout)
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task

    def _encoder_cmd(self) -> list[str]:
        gop = max(1, round(self._fps * KEYFRAME_INTERVAL_SECONDS))
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
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{KEYFRAME_INTERVAL_SECONDS})",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration",
            str(self._frag_duration_ms * 1000),
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            "pipe:1",
        ]

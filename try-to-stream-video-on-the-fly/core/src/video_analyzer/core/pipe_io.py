"""Async I/O helpers shared by FfmpegFrameSource and FfmpegFrameSink's subprocess pipes."""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger


async def terminate_and_wait(proc: asyncio.subprocess.Process, timeout: float) -> None:
    """Terminate proc and wait for it to exit, tolerating a process that has
    already exited on its own."""
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        async with asyncio.timeout(timeout):
            await proc.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


async def read_exact(stream: asyncio.StreamReader, size: int) -> bytes | None:
    try:
        return await stream.readexactly(size)
    except asyncio.IncompleteReadError:
        return None


async def drain_stderr(stream: asyncio.StreamReader, name: str) -> None:
    while True:
        line = await stream.readline()
        if not line:
            break
        logger.info("{}: {}", name, line.decode(errors="replace").rstrip())


async def drain_and_discard(stream: asyncio.StreamReader) -> None:
    """Keep reading (and discarding) from stream until EOF.

    Needed while a subprocess is shutting down: once nobody reads a pipe's
    StreamReader, its buffer fills past the high-water mark and asyncio pauses
    reading from the underlying fd, so the transport never notices the pipe's
    EOF (i.e. the process exiting) — Process.wait() would hang forever even
    after kill() without this.
    """
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break


async def shutdown_process(
    proc: asyncio.subprocess.Process,
    stderr_task: asyncio.Task,
    timeout: float,
) -> None:
    """Terminate an ffmpeg subprocess and stop its stderr drain task.

    Keeps draining stdout while shutting down — see `drain_and_discard`'s
    docstring for why this is required for wait() to ever resolve.
    """
    assert proc.stdout is not None
    drain_task = asyncio.ensure_future(drain_and_discard(proc.stdout))
    await terminate_and_wait(proc, timeout)
    drain_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await drain_task
    stderr_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stderr_task

"""Async I/O helpers shared by Reader and Writer's ffmpeg subprocess pipes."""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger


async def terminate_and_wait(proc: asyncio.subprocess.Process, timeout: float) -> None:
    """Terminate proc and wait for it to exit, tolerating a process that has
    already exited on its own - e.g. it received SIGINT directly as part of
    the same process group during Ctrl-C shutdown, racing with this call."""
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
    StreamReader, its buffer fills past the high-water mark and asyncio
    pauses reading from the underlying fd - which means the transport never
    notices the pipe's EOF (i.e. the process exiting), so Process.wait()
    hangs forever even after kill(). Keeping a drain running until the
    process is confirmed dead avoids that.
    """
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break

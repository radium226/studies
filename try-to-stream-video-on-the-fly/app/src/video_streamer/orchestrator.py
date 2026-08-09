"""Wires InputVideoLoader -> Engine -> Writer -> box-parsing -> Broadcaster together.

Owns the two long-lived asyncio tasks that bridge those components: one
shuttles frames from loader to encoder through the Engine, the other parses
the encoder's fMP4 byte stream into ISO BMFF boxes and publishes them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from video_streamer.broadcaster import Broadcaster
from video_streamer.engine import Engine
from video_streamer.input_video import InputVideoLoader
from video_streamer.iso_bmff import BoxReader, BoxType
from video_streamer.writer import Writer


class Orchestrator:
    def __init__(
        self,
        loader: InputVideoLoader,
        engine: Engine,
        writer: Writer,
        broadcaster: Broadcaster,
    ) -> None:
        self._loader = loader
        self._engine = engine
        self._writer = writer
        self._broadcaster = broadcaster
        self._failure_close_task: asyncio.Task | None = None
        self._failure_exc: BaseException | None = None

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        loader: InputVideoLoader,
        engine: Engine,
        writer: Writer,
        broadcaster: Broadcaster,
    ) -> AsyncIterator[Orchestrator]:
        # Plain create_task, not a TaskGroup: a TaskGroup held open across the
        # yield would, on a child crash, cancel whichever task happened to
        # enter this context (the lifespan task, or an already-finished
        # /api/source handler) and park the exception until teardown. Crashes
        # are surfaced immediately via _on_task_done instead.
        self = cls(loader, engine, writer, broadcaster)
        tasks = [
            asyncio.create_task(self._forward_frames(), name="frame-forward"),
            asyncio.create_task(self._read_writer_output(), name="box-parse"),
        ]
        for task in tasks:
            task.add_done_callback(self._on_task_done)
        try:
            yield self
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._failure_close_task is not None:
                await self._failure_close_task

    @property
    def failure_reason(self) -> str | None:
        """Human-readable reason a pipeline task crashed, or None for a clean
        end (natural EOF). Read by the PipelineManager watchdog to decide
        whether going idle should surface a failure popup."""
        return str(self._failure_exc) if self._failure_exc is not None else None

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        logger.opt(exception=task.exception()).error(
            "pipeline task {!r} crashed", task.get_name()
        )
        # Record the crash so the manager can tell an unexpected failure apart
        # from a natural end-of-source (which never sets this).
        if self._failure_exc is None:
            self._failure_exc = task.exception()
        # Close the broadcaster so clients end cleanly instead of stalling on
        # a pipeline that silently stopped producing.
        if self._failure_close_task is None:
            self._failure_close_task = asyncio.create_task(self._broadcaster.close())

    async def _forward_frames(self) -> None:
        async for frame in self._loader.frames():
            out = await self._engine.process(frame)
            if out is None:
                # Engine is still filling its lookahead delay buffer.
                continue
            try:
                await self._writer.write_frame(out.tobytes())
            except (BrokenPipeError, ConnectionResetError, ValueError):
                break
        # Source exhausted (or encoder gone): EOF the encoder's stdin so it
        # flushes its trailing fragments and EOFs stdout, which lets
        # _read_writer_output finish and close the broadcaster.
        await self._writer.close_stdin()

    async def _read_writer_output(self) -> None:
        box_reader = BoxReader()
        init_boxes: list[bytes] = []
        have_init = False
        pending_moof: bytes | None = None

        while True:
            chunk = await self._writer.read_output_chunk()
            if not chunk:
                if pending_moof is not None:
                    logger.warning("encoder EOF with unpaired trailing moof, dropping")
                # End of stream: wake every client so they finish instead of
                # polling a stream that will never produce again.
                await self._broadcaster.close()
                break
            for box_type, raw in box_reader.feed(chunk):
                if not have_init:
                    init_boxes.append(raw)
                    if box_type == BoxType.MOOV:
                        await self._broadcaster.set_init_segment(b"".join(init_boxes))
                        have_init = True
                        init_boxes = []
                    continue
                if box_type == BoxType.MOOF:
                    pending_moof = raw
                elif box_type == BoxType.MDAT:
                    if pending_moof is not None:
                        await self._broadcaster.publish_fragment(pending_moof + raw)
                        pending_moof = None
                    else:
                        logger.warning("mdat box with no preceding moof, dropping")
                else:
                    logger.debug(
                        "ignoring unexpected top-level box {!r} after init segment",
                        box_type,
                    )

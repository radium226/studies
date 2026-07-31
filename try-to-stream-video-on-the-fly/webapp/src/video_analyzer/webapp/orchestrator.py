"""Wires the CV pipeline run -> box-parsing -> Broadcaster together.

Owns the two long-lived asyncio tasks that bridge them: one runs the whole CV pipeline
(`kernel.Pipeline.run()` already reads frames, detects/tracks/interpolates, writes to the sink,
and broadcasts CV metadata internally — see `kernel/pipeline.py`), the other parses the
encoder's fMP4 byte stream into ISO BMFF boxes and publishes them to the HTTP `Broadcaster`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from video_analyzer import kernel

from .broadcaster import Broadcaster
from .iso_bmff import BoxReader, BoxType
from .overlay_frame_sink import OverlayFrameSink


def _describe_exception(exc: BaseException) -> str:
    """`kernel.Pipeline.run()` runs its six stages in an `asyncio.TaskGroup`, so a crash always
    arrives here wrapped in a `BaseExceptionGroup` — even for a single failing stage. Its own
    `str()` is just "unhandled errors in a TaskGroup (1 sub-exception)"; unwrap to the actual
    stage failure(s) so the browser's error popup says something useful."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe_exception(sub) for sub in exc.exceptions)
    return str(exc)


class Orchestrator:
    def __init__(
        self,
        pipeline: kernel.Pipeline,
        frame_source: kernel.FrameSource,
        frame_sink: OverlayFrameSink,
        broadcaster: Broadcaster,
        stop_token: kernel.StopToken,
    ) -> None:
        self._pipeline = pipeline
        self._frame_source = frame_source
        self._frame_sink = frame_sink
        self._broadcaster = broadcaster
        self._stop_token = stop_token
        self._failure_close_task: asyncio.Task | None = None
        self._failure_exc: BaseException | None = None

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        pipeline: kernel.Pipeline,
        frame_source: kernel.FrameSource,
        frame_sink: OverlayFrameSink,
        broadcaster: Broadcaster,
        stop_token: kernel.StopToken,
    ) -> AsyncIterator[Orchestrator]:
        # Plain create_task, not a TaskGroup: a TaskGroup held open across the yield would, on a
        # child crash, cancel whichever task happened to enter this context (the lifespan task,
        # or an already-finished /api/source handler). Crashes are surfaced immediately via
        # _on_task_done instead.
        self = cls(pipeline, frame_source, frame_sink, broadcaster, stop_token)
        tasks = [
            asyncio.create_task(self._run_pipeline(), name="pipeline-run"),
            asyncio.create_task(self._read_sink_output(), name="box-parse"),
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
        """Human-readable reason a pipeline task crashed, or None for a clean end (natural EOF).
        Read by the PipelineManager watchdog to decide whether going idle should surface a
        failure popup."""
        if self._failure_exc is None:
            return None
        return _describe_exception(self._failure_exc)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        logger.opt(exception=task.exception()).error(
            "pipeline task {!r} crashed", task.get_name()
        )
        # Record the crash so the manager can tell an unexpected failure apart from a natural
        # end-of-source (which never sets this).
        if self._failure_exc is None:
            self._failure_exc = task.exception()
        # Close the broadcaster so clients end cleanly instead of stalling on a pipeline that
        # silently stopped producing.
        if self._failure_close_task is None:
            self._failure_close_task = asyncio.create_task(self._broadcaster.close())

    async def _run_pipeline(self) -> None:
        try:
            await self._pipeline.run(self._frame_source, stop_token=self._stop_token)
        finally:
            # Guarantees the encoder gets EOF'd (flushing trailing fragments) whether run()
            # returns cleanly, raises, or is cancelled during stack teardown — pipeline.run() is
            # opaque, so this has to be a finally rather than a call placed after a hand-rolled
            # forwarding loop.
            await self._frame_sink.close_stdin()

    async def _read_sink_output(self) -> None:
        box_reader = BoxReader()
        init_boxes: list[bytes] = []
        have_init = False
        pending_moof: bytes | None = None

        while True:
            chunk = await self._frame_sink.read_output_chunk()
            if not chunk:
                if pending_moof is not None:
                    logger.warning("encoder EOF with unpaired trailing moof, dropping")
                # End of stream: wake every client so they finish instead of polling a stream
                # that will never produce again.
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

"""Wires Reader -> Engine -> Writer -> box-parsing -> Broadcaster together.

Owns the two long-lived asyncio tasks that bridge those components: one
shuttles frames from decoder to encoder through the Engine, the other parses
the encoder's fMP4 byte stream into ISO BMFF boxes and publishes them.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from video_streamer.broadcaster import Broadcaster
from video_streamer.engine import Engine
from video_streamer.iso_bmff import BoxReader, BoxType
from video_streamer.reader import Reader
from video_streamer.writer import Writer

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        reader: Reader,
        engine: Engine,
        writer: Writer,
        broadcaster: Broadcaster,
        width: int,
        height: int,
    ) -> None:
        self._reader = reader
        self._engine = engine
        self._writer = writer
        self._broadcaster = broadcaster
        self._width = width
        self._height = height
        self._frame_size = width * height * 3
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self._reader.start()
        await self._writer.start()
        self._tasks = [
            asyncio.create_task(self._forward_frames(), name="frame-forward"),
            asyncio.create_task(self._read_writer_output(), name="box-parse"),
        ]

    async def stop(self, timeout: float = 5.0) -> None:
        for task in self._tasks:
            task.cancel()
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*self._tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning("pipeline tasks did not settle within %.1fs of cancellation", timeout)
        await self._reader.stop(timeout=timeout)
        await self._writer.stop(timeout=timeout)
        await self._engine.aclose()
        await self._broadcaster.close()

    async def _forward_frames(self) -> None:
        while True:
            raw = await self._reader.read_frame(self._frame_size)
            if raw is None:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((self._height, self._width, 3))
            frame = await self._engine.process(frame)
            try:
                await self._writer.write_frame(frame.tobytes())
            except (BrokenPipeError, ConnectionResetError, ValueError):
                break

    async def _read_writer_output(self) -> None:
        box_reader = BoxReader()
        init_boxes: list[bytes] = []
        have_init = False
        pending_moof: bytes | None = None

        while True:
            chunk = await self._writer.read_output_chunk()
            if not chunk:
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
                    logger.debug("ignoring unexpected top-level box %r after init segment", box_type)

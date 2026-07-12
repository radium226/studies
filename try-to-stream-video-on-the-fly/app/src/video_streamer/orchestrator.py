"""Wires Reader -> Engine -> Writer -> box-parsing -> Broadcaster together.

Owns the two long-lived asyncio tasks that bridge those components: one
shuttles frames from decoder to encoder through the Engine, the other parses
the encoder's fMP4 byte stream into ISO BMFF boxes and publishes them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        reader: Reader,
        engine: Engine,
        writer: Writer,
        broadcaster: Broadcaster,
        width: int,
        height: int,
    ) -> AsyncIterator[Orchestrator]:
        self = cls(reader, engine, writer, broadcaster, width, height)
        async with asyncio.TaskGroup() as tg:
            forward_task = tg.create_task(self._forward_frames(), name="frame-forward")
            output_task = tg.create_task(self._read_writer_output(), name="box-parse")
            try:
                yield self
            finally:
                forward_task.cancel()
                output_task.cancel()

    async def _forward_frames(self) -> None:
        while True:
            raw = await self._reader.read_frame(self._frame_size)
            if raw is None:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._height, self._width, 3)
            )
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
                    logger.debug(
                        "ignoring unexpected top-level box %r after init segment",
                        box_type,
                    )

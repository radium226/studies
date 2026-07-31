"""Minimal ISO BMFF (MP4) box parser for a raw ffmpeg stdout byte stream.

Pipe reads never align to box boundaries: a single read() can return a partial header, a partial
payload split across many reads, or several complete boxes concatenated together. BoxReader
buffers bytes until whole boxes are available.
"""

from __future__ import annotations

import struct
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import NamedTuple

from loguru import logger

from .broadcaster import Broadcaster


class BoxType(StrEnum):
    FTYP = "ftyp"
    MOOV = "moov"
    MOOF = "moof"
    MDAT = "mdat"


class Box(NamedTuple):
    type: str
    raw: bytes


class BoxReader:
    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Box]:
        """Feed newly-read bytes, return any complete top-level boxes."""
        self._buf += chunk
        boxes: list[Box] = []
        while True:
            box = self._take_one_box()
            if box is None:
                break
            boxes.append(box)
        return boxes

    def _take_one_box(self) -> Box | None:
        buf = self._buf
        if len(buf) < 8:
            return None

        size, type_bytes = struct.unpack_from(">I4s", buf, 0)

        if size == 1:
            if len(buf) < 16:
                return None
            size = struct.unpack_from(">Q", buf, 8)[0]
        elif size == 0:
            raise ValueError(
                "ISO BMFF box with size==0 (EOF-sentinel) is unexpected on a "
                "live, never-finalized ffmpeg stream"
            )

        if len(buf) < size:
            return None

        box_type = type_bytes.decode("ascii", errors="replace")
        raw = bytes(buf[:size])
        del buf[:size]
        return Box(box_type, raw)


async def pump_fragments(
    read_chunk: Callable[[], Awaitable[bytes]], broadcaster: Broadcaster
) -> None:
    """Split an ffmpeg encoder's raw fMP4 stdout into top-level boxes, publish the init segment
    (ftyp..moov) once and every moof+mdat fragment after it to `broadcaster`, and close the
    broadcaster on EOF. Shared by the main stream's box-parse task (`orchestrator.py`) and every
    per-track stream (`track_video_manager.py`) — the pairing logic is identical either way, only
    the source process and destination `Broadcaster` differ."""
    box_reader = BoxReader()
    init_boxes: list[bytes] = []
    have_init = False
    pending_moof: bytes | None = None

    while True:
        chunk = await read_chunk()
        if not chunk:
            if pending_moof is not None:
                logger.warning("encoder EOF with unpaired trailing moof, dropping")
            # End of stream: wake every client so they finish instead of polling a stream that
            # will never produce again.
            await broadcaster.close()
            break
        for box_type, raw in box_reader.feed(chunk):
            if not have_init:
                init_boxes.append(raw)
                if box_type == BoxType.MOOV:
                    await broadcaster.set_init_segment(b"".join(init_boxes))
                    have_init = True
                    init_boxes = []
                continue
            if box_type == BoxType.MOOF:
                pending_moof = raw
            elif box_type == BoxType.MDAT:
                if pending_moof is not None:
                    await broadcaster.publish_fragment(pending_moof + raw)
                    pending_moof = None
                else:
                    logger.warning("mdat box with no preceding moof, dropping")
            else:
                logger.debug(
                    "ignoring unexpected top-level box {!r} after init segment", box_type
                )

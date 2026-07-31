"""Minimal ISO BMFF (MP4) box parser for a raw ffmpeg stdout byte stream.

Pipe reads never align to box boundaries: a single read() can return a partial header, a partial
payload split across many reads, or several complete boxes concatenated together. BoxReader
buffers bytes until whole boxes are available.
"""

from __future__ import annotations

import struct
from enum import StrEnum
from typing import NamedTuple


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

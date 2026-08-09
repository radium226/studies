"""BoxReader: pipe reads never align to box boundaries."""

import struct

import pytest

from video_streamer.iso_bmff import BoxReader


def make_box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def test_single_box_in_one_chunk() -> None:
    reader = BoxReader()
    raw = make_box(b"moof", b"\x01\x02\x03")
    boxes = reader.feed(raw)
    assert [(b.type, b.raw) for b in boxes] == [("moof", raw)]


def test_box_split_across_many_chunks() -> None:
    reader = BoxReader()
    raw = make_box(b"mdat", bytes(range(100)))
    for i in range(0, len(raw) - 1):
        assert reader.feed(raw[i : i + 1]) == []
    boxes = reader.feed(raw[-1:])
    assert [(b.type, b.raw) for b in boxes] == [("mdat", raw)]


def test_multiple_boxes_in_one_chunk_plus_partial_tail() -> None:
    reader = BoxReader()
    a = make_box(b"moof", b"aa")
    b = make_box(b"mdat", b"bbbb")
    c = make_box(b"moof", b"cc")
    boxes = reader.feed(a + b + c[:5])
    assert [(x.type, x.raw) for x in boxes] == [("moof", a), ("mdat", b)]
    boxes = reader.feed(c[5:])
    assert [(x.type, x.raw) for x in boxes] == [("moof", c)]


def test_partial_header_returns_nothing() -> None:
    reader = BoxReader()
    assert reader.feed(b"\x00\x00") == []


def test_extended_size_box() -> None:
    # size==1 means the real 64-bit size follows the type field.
    reader = BoxReader()
    payload = b"x" * 20
    raw = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload
    assert reader.feed(raw[:12]) == []  # not even the extended header yet
    boxes = reader.feed(raw[12:])
    assert [(b.type, b.raw) for b in boxes] == [("mdat", raw)]


def test_unknown_box_type_passes_through() -> None:
    reader = BoxReader()
    raw = make_box(b"styp", b"zz")
    boxes = reader.feed(raw)
    assert [(b.type, b.raw) for b in boxes] == [("styp", raw)]


def test_size_zero_sentinel_raises() -> None:
    reader = BoxReader()
    with pytest.raises(ValueError, match="size==0"):
        reader.feed(struct.pack(">I4s", 0, b"mdat"))

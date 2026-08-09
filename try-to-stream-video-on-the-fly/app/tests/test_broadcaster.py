"""Broadcaster: fan-out semantics, lag detection, close behavior."""

import asyncio

import pytest

from video_streamer.broadcaster import Broadcaster, LaggedError


async def test_wait_returns_next_fragment() -> None:
    b = Broadcaster()
    await b.publish_fragment(b"f0")
    frag = await b.wait_for_next(-1, timeout=0.1)
    assert frag is not None and frag.seq == 0 and frag.data == b"f0"


async def test_wait_skips_to_first_newer_fragment() -> None:
    b = Broadcaster()
    for i in range(3):
        await b.publish_fragment(f"f{i}".encode())
    frag = await b.wait_for_next(0, timeout=0.1)
    assert frag is not None and frag.seq == 1


async def test_wait_times_out_without_new_data() -> None:
    b = Broadcaster()
    await b.publish_fragment(b"f0")
    assert await b.wait_for_next(0, timeout=0.01) is None


async def test_wait_wakes_on_publish() -> None:
    b = Broadcaster()
    waiter = asyncio.create_task(b.wait_for_next(-1, timeout=1.0))
    await asyncio.sleep(0)  # let the waiter block on the condition
    await b.publish_fragment(b"f0")
    frag = await waiter
    assert frag is not None and frag.seq == 0


async def test_lagged_exactly_when_next_needed_fragment_dropped() -> None:
    b = Broadcaster(max_fragments=2)
    for i in range(5):  # retained: seq 3, 4
        await b.publish_fragment(f"f{i}".encode())
    # last_seq=2: fragment 3 is still retained -> not lagged, no gap.
    frag = await b.wait_for_next(2, timeout=0.1)
    assert frag is not None and frag.seq == 3
    # last_seq=1: fragment 2 is gone -> lagged.
    with pytest.raises(LaggedError):
        await b.wait_for_next(1, timeout=0.1)


async def test_close_wakes_waiters_and_returns_none() -> None:
    b = Broadcaster()
    waiter = asyncio.create_task(b.wait_for_next(-1, timeout=1.0))
    await asyncio.sleep(0)
    await b.close()
    assert await waiter is None
    assert b.is_closed


async def test_wait_closed_returns_immediately_if_already_closed() -> None:
    b = Broadcaster()
    await b.close()
    await asyncio.wait_for(b.wait_closed(), timeout=0.1)


async def test_wait_closed_wakes_on_close() -> None:
    b = Broadcaster()
    waiter = asyncio.create_task(b.wait_closed())
    await asyncio.sleep(0)  # let the waiter block on the condition
    assert not waiter.done()
    await b.close()
    await asyncio.wait_for(waiter, timeout=0.1)


async def test_snapshot_for_new_client_gives_init_and_latest_only() -> None:
    b = Broadcaster()
    assert b.snapshot_for_new_client() == (None, None)
    await b.set_init_segment(b"init")
    for i in range(3):
        await b.publish_fragment(f"f{i}".encode())
    init, frag = b.snapshot_for_new_client()
    assert init == b"init"
    assert frag is not None and frag.seq == 2 and frag.data == b"f2"

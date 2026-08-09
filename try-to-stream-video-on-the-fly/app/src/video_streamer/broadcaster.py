"""Async fan-out of a live fMP4 stream to many subscribers.

The box-parse task (an asyncio task, since it does async subprocess I/O) is
the sole producer. Starlette request handlers (also async tasks on the same
event loop) are consumers, awaiting `wait_for_next` directly via an
`asyncio.Condition` - no thread bridging needed since producer and consumers
already share the same event loop.

A bounded deque gives an automatic, allocation-free drop-oldest retention
policy: total memory is capped regardless of subscriber count or speed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NamedTuple


class Fragment(NamedTuple):
    seq: int
    data: bytes


class Snapshot(NamedTuple):
    init_segment: bytes | None
    fragment: Fragment | None


class Broadcaster:
    def __init__(self, max_fragments: int = 15) -> None:
        self._condition = asyncio.Condition()
        self._init_segment: bytes | None = None
        self._fragments: deque[Fragment] = deque(maxlen=max_fragments)
        self._next_seq = 0
        self._closed = False

    @classmethod
    @asynccontextmanager
    async def start(cls, max_fragments: int = 15) -> AsyncIterator[Broadcaster]:
        self = cls(max_fragments)
        try:
            yield self
        finally:
            await self.close()

    async def set_init_segment(self, data: bytes) -> None:
        async with self._condition:
            self._init_segment = data
            self._condition.notify_all()

    async def publish_fragment(self, data: bytes) -> None:
        async with self._condition:
            fragment = Fragment(seq=self._next_seq, data=data)
            self._next_seq += 1
            self._fragments.append(fragment)
            self._condition.notify_all()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def snapshot_for_new_client(self) -> Snapshot:
        """Init segment + only the single latest fragment (true live, not rewind).

        No locking needed: asyncio is single-threaded/cooperative and this
        method never awaits, so it can't interleave with a concurrent
        publish_fragment.
        """
        latest = self._fragments[-1] if self._fragments else None
        return Snapshot(init_segment=self._init_segment, fragment=latest)

    async def wait_for_next(self, last_seq: int, timeout: float) -> Fragment | None:
        """Wait until a fragment newer than last_seq exists, or timeout.

        Returns None on timeout/no-new-data/closed. Raises LaggedError if the
        caller's last_seq has fallen behind the oldest retained fragment -
        callers should treat that as "can't catch up, end this connection."
        """
        async with self._condition:
            try:
                async with asyncio.timeout(timeout):
                    await self._condition.wait_for(
                        lambda: self._closed or self._has_next(last_seq)
                    )
            except TimeoutError:
                return None
            if self._closed:
                return None
            oldest_seq = self._fragments[0].seq
            if last_seq < oldest_seq - 1:
                raise LaggedError(oldest_seq)
            for fragment in self._fragments:
                if fragment.seq > last_seq:
                    return fragment
            return None

    def _has_next(self, last_seq: int) -> bool:
        return bool(self._fragments) and self._fragments[-1].seq > last_seq

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def wait_closed(self) -> None:
        """Block until this broadcaster is closed (returns immediately if already closed)."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._closed)


class LaggedError(Exception):
    def __init__(self, oldest_seq: int) -> None:
        super().__init__(
            f"subscriber fell behind retained fragments (oldest retained seq={oldest_seq})"
        )
        self.oldest_seq = oldest_seq

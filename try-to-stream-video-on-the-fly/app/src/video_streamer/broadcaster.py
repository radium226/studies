"""Thread-safe fan-out of a live fMP4 stream to many async subscribers.

The reader thread (a plain OS thread, since it does blocking subprocess I/O)
is the sole producer. Starlette request handlers (async) are consumers; they
bridge the blocking `threading.Condition` wait via `asyncio.to_thread` so a
slow/waiting subscriber never blocks the event loop or other clients.

A bounded deque gives an automatic, allocation-free drop-oldest retention
policy: total memory is capped regardless of subscriber count or speed.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import NamedTuple


class Fragment(NamedTuple):
    seq: int
    data: bytes


class Snapshot(NamedTuple):
    init_segment: bytes | None
    fragment: Fragment | None


class Broadcaster:
    def __init__(self, max_fragments: int = 15) -> None:
        self._lock = threading.Condition()
        self._init_segment: bytes | None = None
        self._fragments: deque[Fragment] = deque(maxlen=max_fragments)
        self._next_seq = 0
        self._closed = False

    def set_init_segment(self, data: bytes) -> None:
        with self._lock:
            self._init_segment = data
            self._lock.notify_all()

    def publish_fragment(self, data: bytes) -> None:
        with self._lock:
            fragment = Fragment(seq=self._next_seq, data=data)
            self._next_seq += 1
            self._fragments.append(fragment)
            self._lock.notify_all()

    def snapshot_for_new_client(self) -> Snapshot:
        """Init segment + only the single latest fragment (true live, not rewind)."""
        with self._lock:
            latest = self._fragments[-1] if self._fragments else None
            return Snapshot(init_segment=self._init_segment, fragment=latest)

    def wait_for_next(self, last_seq: int, timeout: float) -> Fragment | None:
        """Block (this thread) until a fragment newer than last_seq exists, or timeout.

        Returns None on timeout/no-new-data/closed. Raises LaggedError if the
        caller's last_seq has fallen behind the oldest retained fragment -
        callers should treat that as "can't catch up, end this connection."
        """
        with self._lock:
            deadline_check = self._lock.wait_for(
                lambda: self._closed or self._has_next(last_seq), timeout=timeout
            )
            if not deadline_check or self._closed:
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

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()


class LaggedError(Exception):
    def __init__(self, oldest_seq: int) -> None:
        super().__init__(f"subscriber fell behind retained fragments (oldest retained seq={oldest_seq})")
        self.oldest_seq = oldest_seq

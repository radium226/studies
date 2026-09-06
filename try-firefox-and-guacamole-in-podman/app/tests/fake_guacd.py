"""A stand-in guacd, listening on a real socket.

Real TCP rather than an in-memory fake, because the point of these tests is the
path through Starlette's WebSocket and asyncio's streams -- the parts a fake
channel would skip. It runs on its own event loop in a background thread so it
is reachable from Starlette's TestClient, which owns the main loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from guac_tunnel.protocol import Instruction, InstructionParser


@dataclass
class Session:
    """What one connected client said."""

    received: list[Instruction] = field(default_factory=list)

    def sent(self, opcode: str) -> Instruction:
        for instruction in self.received:
            if instruction.opcode == opcode:
                return instruction
        raise AssertionError(f"the client never sent `{opcode}`; got {self.opcodes}")

    @property
    def opcodes(self) -> list[str]:
        return [instruction.opcode for instruction in self.received]


class _ClientGoneError(Exception):
    """The client hung up. Distinct from 'nothing to read yet'."""


class FakeGuacd:
    """Answers the handshake, then streams whatever it is told to stream."""

    def __init__(self, *, parameters: tuple[str, ...], greeting: str = "") -> None:
        self.parameters = parameters
        #: Sent in the same write as `ready`, mimicking guacd starting to draw
        #: before the client has done anything.
        self.greeting = greeting
        self.sessions: list[Session] = []
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._listening = threading.Event()
        self._connected = threading.Event()
        self._writer: asyncio.StreamWriter | None = None

    @property
    def last_session(self) -> Session:
        assert self.sessions, "nothing ever connected to the fake guacd"
        return self.sessions[-1]

    def push(self, text: str, *, chunk_size: int | None = None) -> None:
        """Send unsolicited data to the connected client, as guacd would.

        ``chunk_size`` writes the text in pieces, reproducing TCP splitting an
        instruction across reads.
        """
        assert self._connected.wait(timeout=5), "no client connected"
        writer, loop = self._writer, self._loop
        assert writer is not None and loop is not None

        pieces = (
            [text]
            if chunk_size is None
            else [text[at : at + chunk_size] for at in range(0, len(text), chunk_size)]
        )
        for piece in pieces:
            loop.call_soon_threadsafe(writer.write, piece.encode())

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._listening.wait(timeout=5), "the fake guacd never started listening"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
            assert not self._thread.is_alive(), "the fake guacd would not shut down"

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        server = self._loop.run_until_complete(asyncio.start_server(self._serve, "127.0.0.1", 0))
        self.port = server.sockets[0].getsockname()[1]
        self._listening.set()
        try:
            self._loop.run_forever()
        finally:
            server.close()
            self._loop.close()

    # -- the handshake, from the other side ---------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = Session()
        self.sessions.append(session)
        self._writer = writer
        self._connected.set()

        parser = InstructionParser()
        pending: deque[Instruction] = deque()

        async def next_instruction() -> Instruction:
            while not pending:
                chunk = await reader.read(4096)
                if not chunk:
                    # EOF. Without this the read loop spins hot forever.
                    raise _ClientGoneError
                pending.extend(parser.feed(chunk.decode()))
            instruction = pending.popleft()
            session.received.append(instruction)
            return instruction

        try:
            await next_instruction()  # select
            writer.write(Instruction("args", ("VERSION_1_5_0", *self.parameters)).encode().encode())
            await writer.drain()

            while (await next_instruction()).opcode != "connect":
                pass

            # `ready` and the first frames share one write, exactly as guacd does.
            writer.write(Instruction("ready", ("$fake-connection",)).encode().encode())
            writer.write(self.greeting.encode())
            await writer.drain()

            while True:  # keep recording, so tests can prove the client->guacd direction
                await next_instruction()
        except (_ClientGoneError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._connected.clear()
            self._writer = None
            writer.close()


@contextmanager
def fake_guacd(
    *, parameters: tuple[str, ...] = ("hostname", "port"), greeting: str = ""
) -> Iterator[FakeGuacd]:
    server = FakeGuacd(parameters=parameters, greeting=greeting)
    server.start()
    try:
        yield server
    finally:
        server.stop()

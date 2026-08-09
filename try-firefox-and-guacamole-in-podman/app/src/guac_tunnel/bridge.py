"""Wiring a browser WebSocket to a guacd TCP socket.

Once the handshake is done there is nothing clever left: guacd and the browser
speak the same protocol, so the tunnel is two pumps copying text in opposite
directions until either end goes away.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from typing import Protocol as TypingProtocol
from typing import Self
from uuid import uuid4

from loguru import logger

from .protocol import Instruction, InstructionParser

#: How much to read from guacd at a time.
_READ_SIZE = 8192

#: Opcode guacamole-common-js reserves for tunnel bookkeeping. The browser
#: expects an instruction with this (empty) opcode carrying the tunnel UUID
#: *before* anything else; without it the tunnel never reaches OPEN and the
#: client sits there silently, connected but never connecting.
INTERNAL_DATA_OPCODE = ""


class GuacdConnection:
    """An `InstructionChannel` backed by a TCP connection to guacd."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._parser = InstructionParser()
        self._queued: deque[Instruction] = deque()

    @classmethod
    async def open(cls, host: str, port: int) -> Self:
        reader, writer = await asyncio.open_connection(host, port)
        return cls(reader, writer)

    async def send(self, instruction: Instruction) -> None:
        self._writer.write(instruction.encode().encode())
        await self._writer.drain()

    async def send_raw(self, text: str) -> None:
        """Forward client text verbatim, without parsing it."""
        self._writer.write(text.encode())
        await self._writer.drain()

    async def read(self) -> Instruction:
        """Read one instruction, waiting for as many chunks as it takes."""
        while not self._queued:
            self._queued.extend(self._parser.feed(await self._read_chunk()))
        return self._queued.popleft()

    async def read_raw(self) -> str:
        """Read whatever guacd has to say, without parsing it."""
        return await self._read_chunk()

    def drain_buffered(self) -> str:
        """Anything read from guacd during the handshake but not consumed.

        guacd starts drawing the moment it sends `ready`, so the same TCP read
        that carried `ready` usually carries the first frames too. Those have
        to reach the browser before the pumps start, or the session opens on a
        blank canvas that only repaints on the next change.
        """
        buffered = "".join(i.encode() for i in self._queued) + self._parser.pending
        self._queued.clear()
        return buffered

    async def close(self) -> None:
        self._writer.close()
        # Already gone, or we are being cancelled: there is nothing left to flush.
        with suppress(OSError, asyncio.CancelledError):
            await self._writer.wait_closed()

    async def _read_chunk(self) -> str:
        chunk = await self._reader.read(_READ_SIZE)
        if not chunk:
            raise ConnectionResetError("guacd closed the connection")
        return chunk.decode()


class ClientSocket(TypingProtocol):  # pragma: no cover - structural typing only
    """The subset of Starlette's WebSocket the bridge needs."""

    async def send_text(self, data: str) -> None: ...

    async def receive_text(self) -> str: ...


def tunnel_uuid_frame(uuid: str | None = None) -> str:
    """The bookkeeping frame guacamole-common-js waits for before opening."""
    return Instruction(INTERNAL_DATA_OPCODE, (uuid or str(uuid4()),)).encode()


async def bridge(connection: GuacdConnection, socket: ClientSocket) -> None:
    """Copy in both directions until either side stops.

    Whichever pump finishes first takes the other down with it: a half-open
    tunnel would leave guacd rendering into a socket nobody reads.
    """

    async def guacd_to_client() -> None:
        while True:
            await socket.send_text(await connection.read_raw())

    async def client_to_guacd() -> None:
        while True:
            await connection.send_raw(await socket.receive_text())

    pumps = [asyncio.create_task(guacd_to_client()), asyncio.create_task(client_to_guacd())]
    try:
        done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if (error := task.exception()) is not None:
                raise error
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        logger.debug("tunnel pumps stopped")

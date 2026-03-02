import array
import asyncio
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

from loguru import logger

MAX_BUFFER_SIZE = 16 * 1024 * 1024


@dataclass
class Frame:
    data: bytes
    fds: list[int] = field(default_factory=list)


@runtime_checkable
class Framing(Protocol):
    def delimit(self, data: bytes) -> bytes: ...
    def extract(self, buffer: bytes) -> tuple[bytes | None, bytes]: ...


class NullCharFraming:
    def delimit(self, data: bytes) -> bytes:
        return data + b"\x00"

    def extract(self, buffer: bytes) -> tuple[bytes | None, bytes]:
        if b"\x00" not in buffer:
            return None, buffer
        idx = buffer.index(b"\x00")
        return buffer[:idx], buffer[idx + 1:]


_CMSG_SPACE_SIZE = 256  # large enough for a few FDs


class Connection:
    def __init__(self, sock: socket.socket, framing: Framing) -> None:
        self._socket = sock
        self._socket.setblocking(False)
        self._framing = framing
        self._buffer = b""
        self._read_waiter: asyncio.Future | None = None
        self._write_waiter: asyncio.Future | None = None

    @classmethod
    def from_socket(cls, sock: socket.socket, framing: Framing = NullCharFraming()) -> "Connection":
        return cls(sock, framing)

    async def _wait_readable(self, loop: asyncio.AbstractEventLoop) -> None:
        fd = self._socket.fileno()
        self._read_waiter = loop.create_future()

        def _on_readable() -> None:
            loop.remove_reader(fd)
            waiter = self._read_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

        loop.add_reader(fd, _on_readable)
        try:
            await self._read_waiter
        except asyncio.CancelledError:
            if self._socket.fileno() != -1:
                loop.remove_reader(self._socket.fileno())
            raise
        finally:
            self._read_waiter = None

    async def _wait_writable(self, loop: asyncio.AbstractEventLoop) -> None:
        fd = self._socket.fileno()
        self._write_waiter = loop.create_future()

        def _on_writable() -> None:
            loop.remove_writer(fd)
            waiter = self._write_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

        loop.add_writer(fd, _on_writable)
        try:
            await self._write_waiter
        except asyncio.CancelledError:
            if self._socket.fileno() != -1:
                loop.remove_writer(self._socket.fileno())
            raise
        finally:
            self._write_waiter = None

    async def send_frame(self, frame: Frame) -> None:
        loop = asyncio.get_running_loop()
        data = self._framing.delimit(frame.data)
        ancdata: list = (
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", frame.fds))]
            if frame.fds else []
        )
        while data:
            try:
                n = self._socket.sendmsg([data], ancdata)
                data = data[n:]
                ancdata = []
            except BlockingIOError:
                await self._wait_writable(loop)

    async def receive_frame(self) -> Frame:
        loop = asyncio.get_running_loop()
        all_fds: list[int] = []

        while True:
            frame_data, self._buffer = self._framing.extract(self._buffer)
            if frame_data is not None:
                return Frame(frame_data, all_fds)

            if len(self._buffer) > MAX_BUFFER_SIZE:
                raise BufferError(
                    f"Receive buffer exceeded {MAX_BUFFER_SIZE} bytes"
                )

            while True:
                try:
                    msg, ancdata, _flags, _addr = self._socket.recvmsg(4096, _CMSG_SPACE_SIZE)
                    break
                except BlockingIOError:
                    await self._wait_readable(loop)
                except ConnectionResetError as e:
                    raise EOFError("Connection reset") from e

            if not msg:
                raise EOFError("Connection closed")

            self._buffer += msg

            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
                    fds_array = array.array("i")
                    fds_array.frombytes(
                        cmsg_data[: len(cmsg_data) - (len(cmsg_data) % fds_array.itemsize)]
                    )
                    all_fds.extend(fds_array)

    async def __aiter__(self) -> AsyncIterator[Frame]:
        while True:
            try:
                yield await self.receive_frame()
            except (EOFError, OSError):
                return

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        fd = self._socket.fileno()
        if fd != -1:
            loop.remove_reader(fd)
            loop.remove_writer(fd)
            if self._read_waiter is not None and not self._read_waiter.done():
                self._read_waiter.cancel()
            if self._write_waiter is not None and not self._write_waiter.done():
                self._write_waiter.cancel()
        self._socket.close()


async def accept_connections(
    path: Path, framing: Framing = NullCharFraming()
) -> AsyncIterator[Connection]:
    loop = asyncio.get_running_loop()
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if path.exists():
        logger.warning("Removing stale socket file: {}", path)
        path.unlink()
    server_sock.bind(str(path))
    server_sock.listen(16)
    server_sock.setblocking(False)
    try:
        while True:
            client_sock, _ = await loop.sock_accept(server_sock)
            yield Connection.from_socket(client_sock, framing)
    finally:
        server_sock.close()


async def open_connection(
    path: Path, framing: Framing = NullCharFraming()
) -> Connection:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setblocking(False)
    await loop.sock_connect(sock, str(path))
    return Connection.from_socket(sock, framing)

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from .protocol import Codec, Emit, Request, Response, validate_event, validate_response
from .transport import Connection, Frame, Framing, NullCharFraming, accept_connections

type RequestHandler[RequestT: Request, EventT, ResponseT: Response] = Callable[[RequestT, list[int], Emit[EventT]], Awaitable[tuple[ResponseT, list[int]]]]


class Server[RequestT: Request, EventT, ResponseT: Response]():
    def __init__(
        self,
        socket_path: Path,
        handler: RequestHandler[RequestT, EventT, ResponseT],
        codec: Codec[RequestT, EventT, ResponseT],
        framing: Framing = NullCharFraming(),
    ) -> None:
        self._socket_path = socket_path
        self._handler = handler
        self._codec = codec
        self._framing = framing
        self._connections: list[Connection] = []

    async def _handle_connection(self, connection: Connection) -> None:
        self._connections.append(connection)

        try:
            async for frame in connection:
                message = self._codec.decode(frame.data)
                match message:
                    case Request() as request:
                        async def emit(event: EventT, fds: list[int] | None = None) -> None:
                            validate_event(request, event)
                            data = self._codec.encode(event)
                            await connection.send_frame(Frame(data, fds or []))

                        response, response_fds = await self._handler(request, frame.fds, emit)
                        validate_response(request, response)
                        await connection.send_frame(
                            Frame(self._codec.encode(response), response_fds)
                        )
        finally:
            if connection in self._connections:
                self._connections.remove(connection)
            await connection.aclose()

    async def serve(self) -> None:
        async for connection in accept_connections(self._socket_path, self._framing):
            asyncio.create_task(self._handle_connection(connection))

    async def wait_forever(self) -> None:
        await asyncio.get_running_loop().create_future()

    async def aclose(self) -> None:
        for connection in list(self._connections):
            await connection.aclose()
        self._connections.clear()
        if self._socket_path.exists():
            self._socket_path.unlink()

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        socket_path: Path,
        handler: RequestHandler[RequestT, EventT, ResponseT],
        codec: Codec[RequestT, EventT, ResponseT],
        framing: Framing = NullCharFraming(),
    ) -> AsyncIterator["Server[RequestT, EventT, ResponseT]"]:
        server: Server[RequestT, EventT, ResponseT] = cls(socket_path, handler, codec, framing)
        try:
            yield server
        finally:
            await server.aclose()

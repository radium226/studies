import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from loguru import logger

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
        logger.info("Client connected (total connections: {})", len(self._connections))

        try:
            async for frame in connection:
                logger.debug("Received frame ({} bytes, {} fds)", len(frame.data), len(frame.fds))
                try:
                    message = self._codec.decode(frame.data)
                except Exception:
                    logger.warning("Failed to decode frame ({} bytes), skipping", len(frame.data))
                    continue

                match message:
                    case Request() as request:
                        logger.info("Received request {} (id={})", type(request).__name__, request.id)

                        async def handle_request(request: RequestT, fds: list[int]) -> None:
                            async def emit(event: EventT, fds: list[int] | None = None) -> None:
                                validate_event(request, event)
                                logger.debug("Emitting event {} for request {}", type(event).__name__, request.id)
                                data = self._codec.encode(event)
                                try:
                                    await connection.send_frame(Frame(data, fds or []))
                                except (OSError, EOFError):
                                    logger.warning("Client disconnected during event emit for request {}", request.id)

                            try:
                                response, response_fds = await self._handler(request, fds, emit)
                                validate_response(request, response)
                                logger.info("Sending response {} for request {} ({} fds)", type(response).__name__, request.id, len(response_fds))
                                await connection.send_frame(
                                    Frame(self._codec.encode(response), response_fds)
                                )
                            except Exception:
                                logger.exception("Handler raised an exception for request {} (id={})", type(request).__name__, request.id)

                        asyncio.create_task(handle_request(request, frame.fds))

                    case _:
                        logger.warning("Received non-Request message: {}", type(message).__name__)
        finally:
            if connection in self._connections:
                self._connections.remove(connection)
            logger.info("Client disconnected (remaining connections: {})", len(self._connections))
            await connection.aclose()

    async def serve(self) -> None:
        logger.info("Server listening on {}", self._socket_path)
        async for connection in accept_connections(self._socket_path, self._framing):
            asyncio.create_task(self._handle_connection(connection))

    async def wait_forever(self) -> None:
        await asyncio.get_running_loop().create_future()

    async def aclose(self) -> None:
        logger.info("Server shutting down ({} active connections)", len(self._connections))
        for connection in list(self._connections):
            await connection.aclose()
        self._connections.clear()
        if self._socket_path.exists():
            self._socket_path.unlink()
            logger.debug("Removed socket file {}", self._socket_path)

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

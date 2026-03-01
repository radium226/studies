import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Generic

from .protocol import Codec, Request, ResponseHandler, Response
from .transport import Connection, Frame, Framing, NullCharFraming, open_connection


class Client[RequestT: Request, EventT, ResponseT: Response]():
    def __init__(
        self,
        connection: Connection,
        codec: Codec[RequestT, EventT, ResponseT],
    ) -> None:
        self._connection = connection
        self._codec = codec
        self._pending: dict[str, tuple[asyncio.Future[None], ResponseHandler[EventT, ResponseT]]] = {}
        self._receive_task: asyncio.Task = asyncio.create_task(self._receive_loop())

    @classmethod
    async def connect(
        cls,
        socket_path: Path,
        codec: Codec[RequestT, EventT, ResponseT],
        framing: Framing = NullCharFraming(),
    ) -> "Client[RequestT, EventT, ResponseT]":
        connection = await open_connection(socket_path, framing)
        return cls(connection, codec)

    async def request(
        self,
        request: RequestT,
        fds: list[int] | None = None,
        handler: ResponseHandler[EventT, ResponseT] | None = None,
    ) -> None:
        if fds is None:
            fds = []
        if handler is None:
            handler = ResponseHandler()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._pending[request.id] = (future, handler)
        await self._connection.send_frame(Frame(self._codec.encode(request), fds))
        await future

    async def _receive_loop(self) -> None:
        async for frame in self._connection:
            message = self._codec.decode(frame.data)
            match message:
                case Response() as response:
                    entry = self._pending.pop(response.request_id, None)
                    if entry is not None:
                        future, response_handler = entry
                        if response_handler.on_response is not None:
                            await response_handler.on_response(response, frame.fds)
                        if not future.done():
                            future.set_result(None)
                case event:
                    for _, (_, response_handler) in list(self._pending.items()):
                        if response_handler.on_event is not None:
                            await response_handler.on_event(event, frame.fds)

    async def aclose(self) -> None:
        self._receive_task.cancel()
        try:
            await self._receive_task
        except asyncio.CancelledError:
            pass
        await self._connection.aclose()

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        socket_path: Path,
        codec: Codec[RequestT, EventT, ResponseT],
        framing: Framing = NullCharFraming(),
    ) -> AsyncIterator["Client[RequestT, EventT, ResponseT]"]:
        client = await cls.connect(socket_path, codec, framing)
        try:
            yield client
        finally:
            await client.aclose()

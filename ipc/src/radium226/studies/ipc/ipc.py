import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from .client import Client
from .protocol import Codec, Request, Response
from .server import RequestHandler, Server
from .transport import Framing, NullCharFraming


class IPC:
    @classmethod
    @asynccontextmanager
    async def open_server[RequestT: Request, EventT, ResponseT: Response](
        cls,
        socket_path: Path,
        codec: Codec[RequestT, EventT, ResponseT],
        *,
        handler: RequestHandler[RequestT, EventT, ResponseT],
        framing: Framing = NullCharFraming(),
    ) -> AsyncIterator[Server[RequestT, EventT,ResponseT]]:
        async with Server.open(socket_path, handler, codec, framing) as server:
            serving_task = asyncio.create_task(server.serve())
            while not socket_path.exists():
                await asyncio.sleep(0)
            try:
                yield server
            finally:
                serving_task.cancel()
                try:
                    await serving_task
                except asyncio.CancelledError:
                    pass

    @classmethod
    @asynccontextmanager
    async def open_client[RequestT: Request, EventT, ResponseT: Response](
        cls,
        socket_path: Path,
        codec: Codec[RequestT, EventT, ResponseT],
        *,
        framing: Framing = NullCharFraming(),
    ) -> AsyncIterator[Client[RequestT, EventT, ResponseT]]:
        async with Client.open(socket_path, codec, framing) as client:
            yield client

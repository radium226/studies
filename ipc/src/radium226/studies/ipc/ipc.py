import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from .client import Client
from .protocol import Codec, Request, Response
from .server import RequestHandler, Server
from .transport import Framing, NullCharFraming


def open_server[RequestT: Request, EventT, ResponseT: Response](
    socket_path: Path,
    codec: Codec[RequestT, EventT, ResponseT],
    *,
    handler: RequestHandler[RequestT, EventT, ResponseT],
    framing: Framing = NullCharFraming(),
) -> AbstractAsyncContextManager[Server[RequestT, EventT, ResponseT]]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[Server[RequestT, EventT, ResponseT]]:
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

    return _ctx()


def open_client[RequestT: Request, EventT, ResponseT: Response](
    socket_path: Path,
    codec: Codec[RequestT, EventT, ResponseT],
    *,
    framing: Framing = NullCharFraming(),
) -> AbstractAsyncContextManager[Client[RequestT, EventT, ResponseT]]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[Client[RequestT, EventT, ResponseT]]:
        async with Client.open(socket_path, codec, framing) as client:
            yield client

    return _ctx()

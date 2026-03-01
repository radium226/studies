import asyncio
import uuid
from pathlib import Path
from typing import Any

import click

from ..ipc import IPC
from ..protocol import ResponseHandler, Emit, Codec
from .messages import Hello, World, Foo, Bar, Event, Request, Response

from pydantic import TypeAdapter

_DEFAULT_SOCKET_PATH = Path("/tmp/radium226-studies-ipc.sock")

_TYPE_ADAPTER = TypeAdapter(Request | Event | Response)


def encode(message: Request | Event | Response) -> bytes:
    return message.model_dump_json().encode()


def decode(data: bytes) -> Request | Event | Response:
    return _TYPE_ADAPTER.validate_json(data.decode())


CODEC = Codec[Request, Event, Response](
    encode=encode,
    decode=decode,
)


@click.group()
def app() -> None:
    pass


@app.command("start-server")
@click.option("--socket-path", default=_DEFAULT_SOCKET_PATH, type=click.Path(path_type=Path), show_default=True)
def start_server(socket_path: Path) -> None:
    async def handler(request: Request, fds: list[int], emit: Emit[Event]) -> tuple[Response, list[int]]:
        match request:
            case Hello(id=id, name=name):
                await emit(Foo(description=f"Received hello from {name}"), [])
                await emit(Bar(description=f"Processing hello from {name}"), [])
                response = World(request_id=id, greeting=f"Hello, {name}!")
                return response, []
            
            case _:
                raise Exception(f"Unknown request: {request}")
    
    
    async def run() -> None:
        async with IPC.open_server(socket_path, CODEC, handler=handler) as server:
            await server.wait_forever()

    asyncio.run(run())


@app.command("hello")
@click.argument("name")
@click.option("--socket-path", default=_DEFAULT_SOCKET_PATH, type=click.Path(path_type=Path), show_default=True)
def hello(name: str, socket_path: Path) -> None:
    async def run() -> None:
        async with IPC.open_client(socket_path, CODEC) as client:
            
            async def on_event(event: Foo | Bar, fds: list[int]) -> None:
                click.echo(f"[event] {event}")


            async def on_response(response: World, fds: list[int]) -> None:
                click.echo(f"[response] {response}")

            await client.request(
                Hello(id=str(uuid.uuid4()), name=name),
                handler=ResponseHandler[Foo | Bar, World](
                    on_response=on_response,
                    on_event=on_event,
                )
            )

    asyncio.run(run())

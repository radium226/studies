"""CLI for ech0 client."""

import asyncio
import sys
import os
from typing import NoReturn

from .service import Ech0Client


async def echo() -> None:
    """Echo a message using the ech0 D-Bus service."""
    async with Ech0Client() as client:
        stdin_fd = sys.stdin.fileno()
        stdout_fd = await client.echo(stdin_fd)
        
        stdout_reader = asyncio.StreamReader()
        stdout_transport, stdout_protocol = await asyncio.get_event_loop().connect_read_pipe(
            lambda: asyncio.streams.StreamReaderProtocol(stdout_reader), os.fdopen(stdout_fd, 'rb')
        )
        
        output_writer_transport, output_writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            lambda: asyncio.streams.FlowControlMixin(), sys.stdout
        )
        output_writer = asyncio.streams.StreamWriter(output_writer_transport, output_writer_protocol, None, asyncio.get_event_loop())
        
        try:
            while True:
                data = await stdout_reader.read(1024)
                if not data:
                    break
                output_writer.write(data)
                await output_writer.drain()
        finally:
            stdout_transport.close()
            output_writer.close()
            # await output_writer.wait_closed()


def app() -> NoReturn:
    asyncio.run(echo())
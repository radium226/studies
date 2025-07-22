"""CLI for ech0 client."""

import argparse
import asyncio
import sys
import os
from typing import NoReturn
import tempfile
from time import sleep

from .service import Ech0Client


async def echo() -> None:
    """Echo a message using the ech0 D-Bus service."""
    async with Ech0Client() as client:
        loop = asyncio.get_event_loop()

        stdout_fd = await client.echo(sys.stdin.fileno())
        
        while True:
            data = await loop.run_in_executor(None, os.read, stdout_fd, 1024)
            if not data:
                break
            await loop.run_in_executor(None, sys.stdout.write, data.decode("utf-8"))

        
    


def app() -> NoReturn:
    asyncio.run(echo())
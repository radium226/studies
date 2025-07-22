"""CLI for ech0 client."""

import argparse
import asyncio
import sys
import os
from typing import NoReturn
import tempfile
from time import sleep

from .service import Ech0Client


async def echo_message(message: str) -> None:
    """Echo a message using the ech0 D-Bus service."""
    async with Ech0Client() as client:
        loop = asyncio.get_event_loop()

        r_fd, w_fd = os.pipe()
        await client.echo(r_fd)
        
        while True:
            await loop.run_in_executor(None, os.write, w_fd, "Hello! ".encode())

        
    


def app() -> NoReturn:
    """Main entry point for ech0 client."""
    parser = argparse.ArgumentParser(
        description="Client for ech0 D-Bus service",
        prog="ech0"
    )
    parser.add_argument(
        "message",
        help="Message to echo"
    )
    
    args = parser.parse_args()
    asyncio.run(echo_message(args.message))
    
    sys.exit(0)
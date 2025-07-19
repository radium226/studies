import asyncio
from click import command, UNPROCESSED, argument
import sys
import signal
from loguru import logger

from dbus_fast import BusType

from ..shared.types import Command, ExitCode
from ..shared.dbus import connect_to_bus

from .core import Client



@command()
@argument("command", nargs=-1, type=UNPROCESSED)
def app(command: Command):
    async def coro() -> ExitCode:
        async with connect_to_bus(BusType.SESSION) as bus:
            client = Client(bus)
            loop = asyncio.get_event_loop()
            
            # FIXME: We should register a signal handler before running the command
            run_control = await client.run(command)
            def sigint_handler():
                logger.info("SIGINT received, aborting...")
                asyncio.create_task(run_control.kill(signal.SIGINT))

            loop.add_signal_handler(signal.SIGINT, sigint_handler)
            return await run_control.wait_for()
        
    exit_code = asyncio.run(coro())
    sys.exit(exit_code)

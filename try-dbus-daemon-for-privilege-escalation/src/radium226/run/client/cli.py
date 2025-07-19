import asyncio
from click import command, UNPROCESSED, argument, option, BadParameter
import sys
import signal
from loguru import logger

from dbus_fast import BusType

from ..shared.types import Command, ExitCode
from ..shared.dbus import connect_to_bus

from .core import Client



@command()
@option("--user", is_flag=True, help="Run in user mode")
@option("--system", is_flag=True, help="Run in system mode (default)")
@argument("command", nargs=-1, type=UNPROCESSED)
def app(user: bool, system: bool, command: Command) -> None:
    # Set defaults and validate
    user = user or False
    system = system or not user
    
    # Validate that exactly one of --user or --system is True
    if user and system:
        raise BadParameter("Cannot specify both --user and --system flags")
    if not user and not system:
        raise BadParameter("Must specify either --user or --system flag")
    
    async def coro() -> ExitCode:
        bus_type = BusType.SESSION if user else BusType.SYSTEM
        async with connect_to_bus(bus_type) as bus:
            client = Client(bus)
            loop = asyncio.get_event_loop()
            
            # FIXME: We should register a signal handler before running the command
            run_control = await client.run(command)
            def sigint_handler() -> None:
                logger.info("SIGINT received, aborting...")
                asyncio.create_task(run_control.kill(signal.SIGINT))

            loop.add_signal_handler(signal.SIGINT, sigint_handler)
            return await run_control.wait_for()
        
    exit_code = asyncio.run(coro())
    sys.exit(exit_code)

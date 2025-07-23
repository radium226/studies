import asyncio
from click import command, UNPROCESSED, argument, option, BadParameter
import sys
import signal
from loguru import logger
import os

from dbus_fast import BusType

from ..shared.types import Command, ExitCode
from ..shared.dbus import connect_to_bus

from .core import Executor, ExecutionContext



@command()
@option("--user", is_flag=True, help="Run in user mode")
@option("--system", is_flag=True, help="Run in system mode (default)")
@argument("command", nargs=-1, type=UNPROCESSED)
def app(user: bool, system: bool, command: Command) -> None:
    # Set defaults and validate
    user = user or os.getuid() != 0
    system = system or not user
    
    # Validate that exactly one of --user or --system is True
    if user and system:
        raise BadParameter("Cannot specify both --user and --system flags")
    if not user and not system:
        raise BadParameter("Must specify either --user or --system flag")
    
    async def coro() -> ExitCode:
        bus_type = BusType.SESSION if user else BusType.SYSTEM
        async with connect_to_bus(bus_type) as bus:
            executor = Executor(bus)
            loop = asyncio.get_event_loop()
            async with executor.execute(ExecutionContext(command=command)) as execution:
                logger.debug("Setting up signal handlers")
                def sigint_handler() -> None:
                    logger.info("SIGINT received, aborting...")
                    asyncio.create_task(execution.kill(signal.SIGTERM))
                loop.add_signal_handler(signal.SIGINT, sigint_handler)
                logger.debug("Waiting for execution to finish")
                exit_code = await execution.wait_for()
                print("We are here !")
                logger.debug("Execution finished with exit code: {exit_code}", exit_code=exit_code)
                return exit_code
        
    exit_code = asyncio.run(coro())
    sys.exit(exit_code)

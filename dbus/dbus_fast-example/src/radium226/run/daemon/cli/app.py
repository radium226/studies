from click import command, option, BadParameter
import asyncio
import os

from dbus_fast import BusType

from pendulum import Duration

from ...shared.dbus import connect_to_bus

from ..core import Executor, ExecutorConfig
from ..dbus import ExecutorInterface



@command()
@option("--user", is_flag=True, help="Run in user mode")
@option("--system", is_flag=True, help="Run in system mode (default)")
def app(user: bool, system: bool) -> None:
    # Set defaults and validate
    user = user or os.getuid() != 0
    system = system or not user
    
    # Validate that exactly one of --user or --system is True
    if user and system:
        raise BadParameter("Cannot specify both --user and --system flags")
    if not user and not system:
        raise BadParameter("Must specify either --user or --system flag")
    
    async def coro() -> None:
        def exception_handler(loop, context):
           print(context)

        # Set the exception handler
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(exception_handler)

        bus_type = BusType.SESSION if user else BusType.SYSTEM
        bus_name = "radium226.run"

        
        async with Executor(ExecutorConfig.default()) as executor, connect_to_bus(bus_type) as bus:
            executor_interface = ExecutorInterface(executor, bus)
            bus.export("/radium226/run/Executor", executor_interface)
            await bus.request_name(bus_name)
            await asyncio.Event().wait()

    asyncio.run(coro())
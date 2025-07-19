from click import command, option, BadParameter
import asyncio

from dbus_fast import BusType

from pendulum import Duration

from ...shared.dbus import connect_to_bus

from ..core import RunnerManager, RunnerManagerConfig
from ..dbus import ServerInterface



@command()
@option("--user", is_flag=True, help="Run in user mode")
@option("--system", is_flag=True, help="Run in system mode (default)")
def app(user: bool, system: bool) -> None:
    # Set defaults and validate
    user = user or False
    system = system or not user
    
    # Validate that exactly one of --user or --system is True
    if user and system:
        raise BadParameter("Cannot specify both --user and --system flags")
    if not user and not system:
        raise BadParameter("Must specify either --user or --system flag")
    
    async def run_command() -> None:
        bus_type = BusType.SESSION if user else BusType.SYSTEM
        bus_name = "radium226.run"

        runner_manager_config = RunnerManagerConfig(
            duration_between_cleanup_of_old_runs=Duration(seconds=5)
        )
        async with RunnerManager(runner_manager_config) as runner_manager, connect_to_bus(bus_type) as bus:
            server_interface = ServerInterface(runner_manager, bus)
            bus.export("/radium226/run/Server", server_interface)
            await bus.request_name(bus_name)
            await runner_manager.wait_forever()

    asyncio.run(run_command())
from click import command
import asyncio

from dbus_fast import BusType

from pendulum import Duration

from ...shared.dbus import connect_to_bus

from ..core import Server, ServerConfig
from ..dbus import ServerInterface



@command()
def app() -> None:
    async def run_command() -> None:
        bus_type = BusType.SESSION
        bus_name = "radium226.run"

        server_config = ServerConfig(
            duration_between_cleanup_of_old_runs=Duration(seconds=5)
        )
        async with Server(server_config) as server, connect_to_bus(bus_type) as bus:
            server_interface = ServerInterface(server, bus)
            bus.export("/radium226/run/Server", server_interface)
            await bus.request_name(bus_name)
            await server.wait_forever()

    asyncio.run(run_command())
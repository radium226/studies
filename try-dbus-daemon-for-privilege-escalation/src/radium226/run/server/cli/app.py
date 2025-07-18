from click import command
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import asyncio
from loguru import logger

from dbus_fast.aio import MessageBus
from dbus_fast import BusType

from pendulum import Duration

from ..core import Server, ServerConfig
from ..dbus import ServerInterface


@asynccontextmanager
async def connect_to_bus(bus_type: BusType) -> AsyncGenerator[MessageBus]:
    bus = MessageBus(bus_type=bus_type)
    logger.debug("Connecting to D-Bus {bus_type}...", bus_type=bus_type)
    await bus.connect()
    logger.debug("Connected! ", bus_type=bus_type)
    try:
        yield bus
    finally:
        logger.debug("Disconnecting from D-Bus {bus_type}...", bus_type=bus_type)
        bus.disconnect()
        logger.debug("Disconnected! ")



@command()
def app():
    async def run_command():
        bus_type = BusType.SESSION
        bus_name = "radium226.run"
        bus = await MessageBus(bus_type=bus_type).connect()

        server_config = ServerConfig(
            duration_between_cleanup_of_old_runs=Duration(seconds=5)
        )
        async with Server(server_config) as server, connect_to_bus(bus_type) as bus:
            server_interface = ServerInterface(server, bus)
            bus.export("/radium226/run/Server", server_interface)
            await bus.request_name(bus_name)
            await server.wait_forever()

    asyncio.run(run_command())
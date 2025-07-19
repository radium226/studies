from contextlib import asynccontextmanager
from typing import AsyncGenerator
from loguru import logger

from dbus_fast.aio import MessageBus
from dbus_fast import BusType



@asynccontextmanager
async def connect_to_bus(bus_type: BusType) -> AsyncGenerator[MessageBus, None]:
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
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from sdbus import (
    sd_bus_open_user,
    set_default_bus,
    SdBus,
)

from loguru import logger



@asynccontextmanager
async def open_bus() -> AsyncGenerator[SdBus, None]:
    logger.trace("open_dbus()")
    bus = sd_bus_open_user()
    try:
        set_default_bus(bus)
        yield bus
    finally:
        bus.close()
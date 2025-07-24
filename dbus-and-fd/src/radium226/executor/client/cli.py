"""CLI for ech0 client."""

import asyncio
import sys
import os
from typing import NoReturn, Any
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from loguru import logger

from click import command, argument, UNPROCESSED


from dbus_fast.aio import MessageBus
from dbus_fast import BusType

from ..shared import redirect



class Execution():
    
    def __init__(self, interface: Any, stdin_fd: int) -> None:
        self.interface = interface
        self.stdin_fd = stdin_fd

    async def send_signal(self, signal: int) -> None:
        await self.interface.call_send_signal(signal)

    async def wait_for(self) -> int:
        stdout_fd = await self.interface.get_stdout()
        async with asyncio.TaskGroup() as tg:
            wait_for_task = tg.create_task(self.interface.call_wait_for())
            tg.create_task(redirect(stdout_fd, sys.stdout.fileno()))
            tg.create_task(redirect(sys.stdin.fileno(), self.stdin_fd))

        return wait_for_task.result()
    


class Service():

    bus: MessageBus

    def __init__(self, bus: MessageBus, interface: Any) -> None:
        self.bus = bus
        self.interface = interface


    async def execute(self, command: list[str]) -> Execution:
        stdin_read_fd, stdin_write_fd = os.pipe()
        
        execution_path = await self.interface.call_execute(command, stdin_read_fd)
        logger.info(f"Execution started at path: {execution_path}")
        execution_introspection = await self.bus.introspect("radium226.Executor", execution_path)
        logger.info(f"Introspection for Execution interface: {execution_introspection}")
        execution_proxy = self.bus.get_proxy_object("radium226.Executor", execution_path, execution_introspection)
        logger.info(f"Got proxy object for Execution interface: {execution_proxy}")
        execution_interface = execution_proxy.get_interface("radium226.Execution")
        logger.info(f"Got Execution interface: {execution_interface}")
        execution = Execution(execution_interface, stdin_write_fd)
        return execution



@asynccontextmanager
async def start_service() -> AsyncGenerator[Service, None]:
    bus = MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True)
    
    logger.info("Connecting to D-Bus...")
    await bus.connect()
    
    introspection = await bus.introspect("radium226.Executor", "/radium226/Executor")
    proxy_object = bus.get_proxy_object("radium226.Executor", "/radium226/Executor", introspection)
    interface = proxy_object.get_interface("radium226.Executor")
    
    service = Service(bus, interface)
    
    try:
        yield service
    finally:
        logger.info("Disconnecting bus...")
        bus.disconnect()



@command()
@argument("command", nargs=-1, type=UNPROCESSED)
def app(command) -> NoReturn:
    async def coro() -> None:
        async with start_service() as service:
            logger.info("Executor client started! ")
            
            logger.info(f"Executing command: {command}")
            execution = await service.execute(list(command))
            
            logger.info("Wait for execution to finish...")
            exit_code = await execution.wait_for()
    
    asyncio.run(coro())
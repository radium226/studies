"""CLI for ech0 client."""

import asyncio
import sys
import os
from typing import NoReturn, Any
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from loguru import logger
import signal

from click import command, argument, UNPROCESSED


from dbus_fast.aio import MessageBus
from dbus_fast import BusType

from ..shared import redirect



class Execution():
    
    def __init__(self, interface: Any) -> None: # , redirect_stdin_task) -> None:
        self.interface = interface
        # self.redirect_stdin_task = redirect_stdin_task

    async def send_signal(self, signal: int) -> None:
        logger.debug("Sending signal: {signal}", signal=signal)
        await self.interface.call_send_signal(signal)


    async def wait_for(self) -> int:
        stdout_fd = await self.interface.get_stdout()
        async with asyncio.TaskGroup() as tg:
            wait_for_task = tg.create_task(self.interface.call_wait_for())
            tg.create_task(redirect(stdout_fd, sys.stdout.fileno(), "STDOUT"))
            # tg.create_task(await self.redirect_stdin_task)

        return wait_for_task.result()
    


class Service():

    bus: MessageBus

    def __init__(self, bus: MessageBus, interface: Any) -> None:
        self.bus = bus
        self.interface = interface


    async def execute(self, command: list[str]) -> Execution:
        # stdin_read_fd, stdin_write_fd = os.pipe()
        # redirect_stdin_task = asyncio.create_task(redirect(sys.stdin.fileno(), stdin_write_fd, "STDIN"))

        execution_path = await self.interface.call_execute(command, sys.stdin.fileno()) # , stdin_read_fd)
        execution_introspection = await self.bus.introspect("radium226.Executor", execution_path)
        execution_proxy = self.bus.get_proxy_object("radium226.Executor", execution_path, execution_introspection)
        execution_interface = execution_proxy.get_interface("radium226.Execution")
        execution = Execution(execution_interface) # , redirect_stdin_task)
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

            logger.debug("Registering to signal handler for SIGINT...")
            def handle_sigint_signal() -> None:
                logger.info("SIGINT signal received, sending signal to execution...")
                asyncio.create_task(execution.send_signal(signal.SIGINT))

            asyncio.get_event_loop().add_signal_handler(signal.SIGINT, handle_sigint_signal)

            logger.info("Wait for execution to finish...")
            exit_code = await execution.wait_for()
            logger.info(f"Execution finished with exit code: {exit_code}")
            return exit_code
    
    logger.info("sys.stdin.isatty(): {isatty}", isatty=sys.stdin.isatty())

    exit_code = asyncio.run(coro())
    sys.exit(exit_code)
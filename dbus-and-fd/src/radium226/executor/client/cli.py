"""CLI for ech0 client."""

import asyncio
import sys
import os
from typing import NoReturn, Any, Coroutine
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from loguru import logger
import signal
from enum import StrEnum, auto
import pty
from dataclasses import dataclass

from click import command, argument, UNPROCESSED


from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message

from ..shared import redirect, Mode





@dataclass
class Execution():
    
    wait_for: Callable[[], Coroutine[Any, Any, int]]
    send_signal: Callable[[int], Coroutine[Any, Any, None]]    


class Service():

    bus: MessageBus

    def __init__(self, bus: MessageBus, interface: Any) -> None:
        self.bus = bus
        self.interface = interface


    async def execute(self, command: list[str]) -> Execution:
        # mode = Mode.for_stdin()
        # match mode:
        #     case Mode.TTY:
        #         logger.debug("Using TTY mode for stdin...")
        #         stdin_read_fd, stdin_write_fd = pty.openpty()
        #     case Mode.PIPE:
        #         logger.debug("Using PIPE mode for stdin...")
        #         stdin_read_fd, stdin_write_fd = os.pipe()
        #     case _:
        #         raise ValueError(f"Unknown mode: {mode}")
        
        # logger.debug(f"Executing command: {command} with stdin_fd: {stdin_read_fd}")
        # stdin_redirection = await redirect(sys.stdin.fileno(), stdin_write_fd)# logger.debug("Starting to redirect stdout...")
        # stdout_read_fd = await execution_interface.get_stdout()
        # stdout_redirection = await redirect(stdout_read_fd, sys.stdout.fileno())
        # logger.debug(f"Redirected stdout from fd {stdout_read_fd} to sys.stdout")

        mode = Mode.for_stdout()
        match mode:
            case Mode.TTY:
                logger.debug("Using TTY mode for stdout...")
                stdout_read_fd, stdout_write_fd = pty.openpty()
            case Mode.PIPE:
                logger.debug("Using PIPE mode for stdout...")
                stdout_read_fd, stdout_write_fd = os.pipe()
            case _:
                raise ValueError(f"Unknown mode: {mode}")
            
        logger.debug("Starting to redirect stdout...")
        stdout_redirection = await redirect(stdout_read_fd, sys.stdout.fileno())
        logger.debug(f"Redirected stdout from fd {stdout_read_fd} to sys.stdout")

        mode = Mode.for_stdin()
        match mode:
            case Mode.TTY:
                logger.debug("Using TTY mode for stdin...")
                stdin_read_fd, stdin_write_fd = pty.openpty()
            case Mode.PIPE:
                logger.debug("Using PIPE mode for stdin...")
                stdin_read_fd, stdin_write_fd = os.pipe()
            case _:
                raise ValueError(f"Unknown mode: {mode}")
            

        print(f"stdin_read_fd: {stdin_read_fd}, stdin_write_fd: {stdin_write_fd}")
        logger.debug(f"Executing command: {command} with stdin_fd: {stdin_read_fd} and stdout_fd: {stdout_write_fd}")
        stdin_redirection = await redirect(sys.stdin.fileno(), stdin_write_fd)
        logger.debug("Redirected stdin to the execution's stdin")

        reply = await self.bus.call(
                Message(
                    destination='radium226.Executor',
                    path='/radium226/Executor',
                    interface='radium226.Executor',
                    member='Execute',
                    signature="as(hh)",
                    body=[command, [0, 1]],
                    unix_fds=[stdin_read_fd, stdout_write_fd]
                )
            )


        execution_path = await self.interface.call_execute(command, [stdin_read_fd, stdout_write_fd])
        execution_introspection = await self.bus.introspect("radium226.Executor", execution_path)
        execution_proxy = self.bus.get_proxy_object("radium226.Executor", execution_path, execution_introspection)
        execution_interface = execution_proxy.get_interface("radium226.Execution")
        logger.debug(f"Execution interface obtained at path: {execution_path}")

        # logger.debug("Starting to redirect stdout...")
        # stdout_read_fd = await execution_interface.get_stdout()
        # stdout_redirection = await redirect(stdout_read_fd, sys.stdout.fileno())
        # logger.debug(f"Redirected stdout from fd {stdout_read_fd} to sys.stdout")

        async def wait_for() -> int:
            logger.debug("Waiting for execution to finish...")
            exit_code = await execution_interface.call_wait_for()
            await stdin_redirection.abort()
            await stdout_redirection.abort()
            
            await stdin_redirection.wait_for()
            await stdout_redirection.wait_for()

            return exit_code
        
        async def send_signal(signal: int) -> None:
            logger.debug(f"Sending signal {signal} to execution...")
            await execution_interface.call_send_signal(signal)
        
        execution = Execution(wait_for=wait_for, send_signal=send_signal)
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

    exit_code = asyncio.run(coro())
    sys.exit(exit_code)
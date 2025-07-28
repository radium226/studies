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


from sdbus import (
    DbusInterfaceCommonAsync, 
    dbus_method_async,
    request_default_bus_name_async,
    sd_bus_open_user, 
    request_default_bus_name_async,
    SdBus, 
    set_default_bus,
)

from ..shared import redirect, Mode

from ..daemon.service import ExecutorInterface, ExecutionInterface, CommandNotFoundError




@dataclass
class Execution():
    
    wait_for: Callable[[], Coroutine[Any, Any, int]]
    send_signal: Callable[[int], Coroutine[Any, Any, None]]    


class Service():

    async def execute(self, command: list[str]) -> Execution:

        signal_sent: bool = False

        logger.info("Retrieving proxy for ExecutorInterface...")
        executor_interface = ExecutorInterface.new_proxy(
            "radium226.Executor",
            "/radium226/Executor",
        )
        logger.info("Retrieved! ")

        logger.debug(f"Executing command: {command}")
        execution_path = await executor_interface.execute(
            command,
            sys.stdin.fileno(), 
            sys.stdout.fileno(),
        )
        logger.debug(f"Executed! ")

        execution_interface = ExecutionInterface.new_proxy(
            "radium226.Executor",
            execution_path,
        )

        async def wait_for() -> Coroutine[Any, Any, int]:
            nonlocal signal_sent
            timeout = 0.5
            while True:
                exit_code = None
                try:
                    logger.debug("Waiting for ExitCode property change...")
                    properties_changed = execution_interface.properties_changed.catch()
                    event = await asyncio.wait_for(anext(properties_changed), timeout=timeout)
                    interface_name, changed_properties, _ = event
                    if interface_name == "radium226.Execution":
                        if "ExitCode" in changed_properties:
                            (_, (_, exit_code)) = changed_properties["ExitCode"]
                            exit_code = exit_code if exit_code != "" else None
                            logger.debug(f"ExitCode changed: {exit_code}")
                except asyncio.TimeoutError:
                    logger.debug("Timeout waiting for ExitCode property change, checking status...")
                    _, exit_code = await execution_interface.exit_code.get_async()
                    exit_code = exit_code if exit_code != "" else None

                timeout = 15

                logger.debug(f"Exit code: {exit_code}")
                if exit_code is not None:
                    if signal_sent:
                        exit_code = 128 - exit_code
                    return exit_code

        
        async def send_signal(signal: int) -> Coroutine[Any, Any, None]:
            nonlocal signal_sent
            logger.trace(f"Sending signal {signal} to execution...")
            await execution_interface.send_signal(signal)
            signal_sent = True
            
        execution = Execution(wait_for=wait_for, send_signal=send_signal)
        return execution


@asynccontextmanager
async def start_service() -> AsyncGenerator[Service, None]:
    logger.info("Opening D-Bus...")
    bus = sd_bus_open_user()
    logger.info("Opened! ")
    
    logger.info("Setting as default bus...")
    set_default_bus(bus)
    logger.info("Set! ")

    service = Service()
    
    try:
        yield service
    finally:
        logger.info("Closing D-Bus...")
        bus.close()
        logger.info("Closed! ")



@command()
@argument("command", nargs=-1, type=UNPROCESSED)
def app(command) -> NoReturn:
    async def coro() -> None:
        async with start_service() as service:
            logger.info("Executor client started! ")
            
            logger.info(f"Executing command: {command}")
            try:
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
            except CommandNotFoundError:
                logger.error(f"Command not found: {" ".join(command)}")
                sys.exit(127)

    exit_code = asyncio.run(coro())
    sys.exit(exit_code)
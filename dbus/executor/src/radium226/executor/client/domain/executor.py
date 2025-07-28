from typing import (
    AsyncGenerator,
    Coroutine,
    Any,
    Self, 
)
import sys
import asyncio
from contextlib import asynccontextmanager

from sdbus import SdBus

from loguru import logger

from ...shared.dbus.executor_interface import ExecutorInterface
from ...shared.dbus.execution_interface import ExecutionInterface

from .execution import Execution


class Executor():

    _executor_interface: ExecutorInterface

    def __init__(self, executor_interface: ExecutorInterface) -> None:
        self._executor_interface = executor_interface


    @classmethod
    @asynccontextmanager
    async def connect_to(cls, bus: SdBus) -> AsyncGenerator[Self, None]:
        logger.trace("connect_to({bus})", bus=bus)
        executor_interface = ExecutorInterface.new_proxy(
            "radium226.Executor",
            "/radium226/Executor",
        )
        yield cls(executor_interface)

    async def execute(self, command: list[str]) -> Execution:
        print(self._executor_interface)
        execution_path = await self._executor_interface.execute(
            command,
            sys.stdin.fileno(), 
            sys.stdout.fileno(),
        )

        execution_interface = ExecutionInterface.new_proxy(
            "radium226.Executor",
            execution_path,
        )

        async def wait_for() -> Coroutine[Any, Any, int]:
            logger.trace("wait_for()")

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
                    return exit_code

        
        async def send_signal(signal: int) -> Coroutine[Any, Any, None]:
            logger.trace(f"send_signal({signal})")
            await execution_interface.send_signal(signal)
            
        execution = Execution(wait_for=wait_for, send_signal=send_signal)
        return execution
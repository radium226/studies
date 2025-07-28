from mailbox import Message
from typing import AsyncGenerator, Callable, Coroutine, Any
import asyncio
import os
from contextlib import asynccontextmanager
import traceback

from loguru import logger

from sdbus import (
    DbusInterfaceCommonAsync, 
    dbus_method_async,
    request_default_bus_name_async,
    sd_bus_open_user, 
    request_default_bus_name_async,
    SdBus, 
    set_default_bus,
    DbusObjectManagerInterfaceAsync,
    dbus_property_async,
    DbusFailedError,
)

from ..shared import redirect


class ExecutionInterface(
    DbusInterfaceCommonAsync,
    interface_name="radium226.Execution",
):

    _send_signal: Callable[[int], Coroutine[Any, Any, None]]
    
    _status: str = "running"

    _exit_code: tuple[str, str | int] = ("s", "")

    def __init__(self, 
        send_signal: Callable[[int], Coroutine[Any, Any, None]], 
    ) -> None:
        super().__init__()
        self._send_signal = send_signal
    
    @dbus_method_async(
        input_signature="i",
        method_name="SendSignal",
    )
    async def send_signal(self, signal: int) -> None:
        logger.debug(f"SendSignal method called with signal: {signal}")
        await self._send_signal(signal)

    @dbus_property_async(
        property_signature='s',
        property_name="Status",
    )
    def status(self) -> str:
        return self._status
    
    @status.setter_private
    def _set_status(self, value: str) -> None:
        logger.debug(f"Setting status to: {value}")
        self._status = value

    @dbus_property_async(
        property_signature='v',
        property_name="ExitCode",
    )
    def exit_code(self) -> tuple[str, str | int]:
        return self._exit_code
    
    @exit_code.setter_private
    def _set_exit_code(self, value: tuple[str, str | int]) -> None:
        self._exit_code = value


class CommandNotFoundError(DbusFailedError):
    dbus_error_name = "radium226.CommandNotFound"
        


class ExecutorInterface(
    DbusObjectManagerInterfaceAsync,
    interface_name="radium226.Executor",
):

    def __init__(self) -> None:
        super().__init__()
        

    @dbus_method_async(
        input_signature="ashh",
        method_name="Execute",
        result_signature="o",  # Object path
    )
    async def execute(self, command: list[str], stdin_fd: int, stdout_fd: int) -> str: 
        logger.info(f"Executing command: {command} with stdin_fd: {stdin_fd} and stdout_fd: {stdout_fd}")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=stdin_fd,
                stdout=stdout_fd,
            )
        except Exception as e:
            logger.error(f"Failed to execute command: {e}")
            raise CommandNotFoundError()

        async def send_signal(signal: int) -> None:
            logger.info(f"Sending signal {signal} to process {process.pid}")
            process.send_signal(signal)
           
        execution_path = f"/radium226/Executor/{process.pid}"
        execution_interface = ExecutionInterface(
            send_signal=send_signal,
        )

        async def notify_completion():
            logger.info(f"Waiting for process {process.pid} to finish...")
            exit_code = await process.wait()
            logger.info(f"Process {process.pid} finished with exit code: {exit_code}")
            await execution_interface.exit_code.set_async(("i", exit_code))
            await execution_interface.status.set_async("completed")

        asyncio.create_task(notify_completion())
        

        logger.info(f"Exporting ExecutionInterface to D-Bus (at {execution_path})")
        self.export_with_manager(execution_path, execution_interface)
        logger.info("Exported! ")

        return execution_path


@asynccontextmanager
async def start_service() -> AsyncGenerator[Callable[[], None], None]:
    logger.info("Opening D-Bus...")
    bus = sd_bus_open_user()
    logger.info("Opened! ")

    logger.info("Setting as default bus...")
    set_default_bus(bus)
    logger.info("Set! ")

    logger.info("Acquiring default bus name...")
    await bus.request_name_async("radium226.Executor", 0)
    logger.info("Acquired! ")

    logger.info("Exporting Executor to D-Bus...")
    interface = ExecutorInterface()
    interface.export_to_dbus("/radium226/Executor")
    logger.info("Exported! ")
    
    async def wait_for():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("Wait cancelled! ")
    try:
        yield wait_for
    finally:
        logger.info("Disconnecting bus...")
        bus.close()
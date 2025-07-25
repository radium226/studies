from typing import AsyncGenerator, Callable, Coroutine, Any
import asyncio
import os
from contextlib import asynccontextmanager

from loguru import logger

from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method, dbus_property, PropertyAccess, signal

from ..shared import redirect


class ExecutionInterface(ServiceInterface):

    stdout_fd: int

    send_signal: Callable[[int], Coroutine[Any, Any, None]]
    wait_for: Callable[[], Coroutine[Any, Any, int]]

    def __init__(self, 
        stdout_fd: int,             
        send_signal: Callable[[int], Coroutine[Any, Any, None]], 
        wait_for: Callable[[], Coroutine[Any, Any, int]],
    ) -> None:
        super().__init__("radium226.Execution")
        self.stdout_fd = stdout_fd
        self.send_signal = send_signal
        self.wait_for = wait_for

    @dbus_property(PropertyAccess.READ)
    async def Stdout(self) -> "h":
        return self.stdout_fd
    
    @method()
    async def SendSignal(self, signal: "i") -> None:
        await self.send_signal(signal)

    @method()
    async def WaitFor(self) -> "i":
        return await self.wait_for()
    
    @signal
    async def StdoutClosed(self) -> None:
        """Signal emitted when stdout is closed."""
        logger.debug("Stdout closed signal emitted.")
        pass

    @method()
    async def CloseStdin(self) -> None:
        """Close the stdin file descriptor."""
        logger.debug("Closing stdin file descriptor.")
        os.close(self.stdout_fd)
        await self.StdoutClosed()



class ExecutorInterface(ServiceInterface):

    bus: MessageBus

    def __init__(self, bus: MessageBus) -> None:
        super().__init__("radium226.Executor")
        self.bus = bus

    @method()
    async def Execute(self, command: "as", stdin_fd: "h") -> "o":  # type: ignore[misc]
        try:
            logger.info(f"Executing command: {command} with stdin_fd: {stdin_fd}")
            stdout_read_fd, stdout_write_fd = os.pipe()


            stdin_read_fd, stdin_write_fd = os.pipe()

            asyncio.create_task(redirect(stdin_fd, stdin_write_fd))

            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=stdin_read_fd,
                stdout=stdout_write_fd,
            )
            os.close(stdout_write_fd)  # Close the write end after passing it to the process

            async def send_signal(signal: int) -> None:
                execution_path = f"/radium226/Execution/{process.pid}"
                await process.send_signal(signal)

            async def wait_for() -> int:
                return await process.wait()
            
            logger.info(f"Process started with PID: {process.pid}")
            execution_path = f"/radium226/Execution/{process.pid}"
            execution_interface = ExecutionInterface(
                stdout_fd=stdout_read_fd,
                send_signal=send_signal,
                wait_for=wait_for,
            )

            logger.info(f"Exporting Execution interface at {execution_path}")
            self.bus.export(execution_path, execution_interface)
            return execution_path


        except Exception as e:
            logger.error(f"Failed to execute command {command}: {e}")
            raise


@asynccontextmanager
async def start_service() -> AsyncGenerator[Callable[[], None], None]:
    bus = MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True)
    
    logger.info("Connecting to D-Bus...")
    await bus.connect()
    
    logger.info("Exporting Executor interface...")
    interface = ExecutorInterface(bus)
    bus.export("/radium226/Executor", interface)
    await bus.request_name("radium226.Executor")
    
    async def wait_for():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("Wait cancelled! ")
    try:
        yield wait_for
    finally:
        logger.info("Disconnecting bus...")
        bus.disconnect()
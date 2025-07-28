import asyncio
from asyncio import TaskGroup
from typing import Any, Callable
from asyncio import Future
from asyncio.streams import StreamReader, StreamReaderProtocol, FlowControlMixin, StreamWriter
from loguru import logger
import sys
import os
from pathlib import Path
from typing import AsyncGenerator

from dbus_fast.aio import MessageBus
from dbus_fast import Message, MessageType
import signal

from ..shared import Command, ExitCode, Signal, ExecutionContext

from contextlib import asynccontextmanager



class Execution:
    
    def __init__(self, dbus_bus: MessageBus, dbus_path: str):
        self.dbus_bus = dbus_bus
        self.dbus_path = dbus_path
        
    async def wait_for(self) -> ExitCode:
        dbus_introspection = await self.dbus_bus.introspect("radium226.run", self.dbus_path)
        dbus_proxy_object = self.dbus_bus.get_proxy_object("radium226.run", self.dbus_path, dbus_introspection)
        dbus_interface = dbus_proxy_object.get_interface("radium226.run.Execution")

        exit_code = await dbus_interface.call_wait_for()
        logger.debug("Execution finished with exit code: {exit_code}", exit_code=exit_code)
        return exit_code
    
    async def kill(self, signal: Signal) -> None:
        dbus_introspection = await self.dbus_bus.introspect("radium226.run", self.dbus_path)
        dbus_proxy_object = self.dbus_bus.get_proxy_object("radium226.run", self.dbus_path, dbus_introspection)
        dbus_interface = dbus_proxy_object.get_interface("radium226.run.Execution")

        logger.debug("Killing execution with signal: {signal}", signal=signal)
        await dbus_interface.call_kill(signal)




async def redirect_to(fd: int, stream: Any) -> None:
    try:
        loop = asyncio.get_event_loop()
        while True:
            # Run os.read in executor to avoid blocking
            data = await loop.run_in_executor(None, os.read, fd, 1024)
            if data:
                await loop.run_in_executor(None, stream.buffer.write, data)
                await loop.run_in_executor(None, stream.buffer.flush)
            else:
                break
    except asyncio.CancelledError:
        logger.debug("Redirect task cancelled")

class Executor():

    dbus_bus: MessageBus

    def __init__(self, dbus_bus: MessageBus):
        self.dbus_bus = dbus_bus


    @asynccontextmanager
    async def execute(self, context: ExecutionContext | Command) -> AsyncGenerator[Execution, None]:
        # Get the introspection data and create a proxy object
        executor_dbus_introspection = await self.dbus_bus.introspect("radium226.run", "/radium226/run/Executor")
        executor_dbus_proxy_object = self.dbus_bus.get_proxy_object("radium226.run", "/radium226/run/Executor", executor_dbus_introspection)
        executor_dbus_interface = executor_dbus_proxy_object.get_interface("radium226.run.Executor")
        execution_dbus_path = await executor_dbus_interface.call_execute(
            context.command,
            context.user_id,
            str(context.current_working_folder_path),
            context.environment_variables
        )
        logger.debug("execution_dbus_path={execution_dbus_path}", execution_dbus_path=execution_dbus_path)

        execution_dbus_introspection = await self.dbus_bus.introspect("radium226.run", execution_dbus_path)
        execution_dbus_proxy_object = self.dbus_bus.get_proxy_object("radium226.run", execution_dbus_path, execution_dbus_introspection)
        execution_dbus_interface = execution_dbus_proxy_object.get_interface("radium226.run.Execution")
        
        stdout = await execution_dbus_interface.get_stdout()
        logger.debug("stdout={stdout}", stdout=stdout)

        stderr = await execution_dbus_interface.get_stderr()
        logger.debug("stderr={stderr}", stderr=stderr)

        
        redirect_task = asyncio.gather(
            redirect_to(stdout, sys.stdout),
            redirect_to(stderr, sys.stderr),
            return_exceptions=True,
        )

        execution = Execution(
            dbus_bus=self.dbus_bus,
            dbus_path=execution_dbus_path
        )
        yield execution
        
        logger.debug("Execution finished, cleaning up...")
        redirect_task.cancel()
        logger.debug("Waiting for redirect task to finish...")
        try:
            await redirect_task
        except asyncio.CancelledError:
            logger.debug("Redirect task cancelled")
            
        logger.debug("Execution cleanup done")

        
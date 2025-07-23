import asyncio
from asyncio import TaskGroup
from typing import Any, Callable
from asyncio import Future
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
    stdout_reader = asyncio.StreamReader()
    stdout_transport, stdout_protocol = await asyncio.get_event_loop().connect_read_pipe(
        lambda: asyncio.streams.StreamReaderProtocol(stdout_reader), os.fdopen(fd, 'rb')
    )
    
    output_writer_transport, output_writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(), 
        stream
    )
    output_writer = asyncio.streams.StreamWriter(output_writer_transport, output_writer_protocol, None, asyncio.get_event_loop())
    
    try:
        while True:
            data = await stdout_reader.read(1024)
            if not data:
                break
            output_writer.write(data)
            await output_writer.drain()
    except Exception as e:
        print(f"Error redirecting output: {e}")
    finally:
        stdout_transport.close()
        output_writer.close()
        # await output_writer.wait_closed()
        


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
            redirect_to(stderr, sys.stderr)
        )

        execution = Execution(
            dbus_bus=self.dbus_bus,
            dbus_path=execution_dbus_path
        )
        yield execution


        logger.debug("Execution finished, cleaning up...")
        redirect_task.cancel()
        await redirect_task

        
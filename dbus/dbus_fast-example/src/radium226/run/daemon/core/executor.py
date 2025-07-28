import asyncio
from asyncio import get_event_loop
from asyncio.subprocess import Process
import os
import signal

from loguru import logger

from .types import ExecutorConfig
from .execution import Execution
from ...shared import ExecutionContext, ExecutionID, FileDescriptor





class Executor:

    _config: ExecutorConfig

    _executions: dict[ExecutionID, Execution] = {}

    def __init__(self, config: ExecutorConfig):
        self._config = config

    async def execute(self, context: ExecutionContext) -> Execution:
        loop = get_event_loop()
        
        stderr_read_fd, stderr_write_fd = await loop.run_in_executor(None, os.pipe)
        stdout_read_fd, stdout_write_fd = await loop.run_in_executor(None, os.pipe)
        stdin_read_fd, stdin_write_fd = await loop.run_in_executor(None, os.pipe)

        def switch_uid():
            os.setuid(context.user_id)

        process = await asyncio.create_subprocess_exec(
            *context.command,
            stdin=stdin_read_fd,
            stdout=stdout_write_fd,
            stderr=stderr_write_fd,
            env=context.environment_variables,
            cwd=str(context.current_working_folder_path),
            preexec_fn=switch_uid,
        )
        await loop.run_in_executor(None, os.close, stdout_write_fd)
        await loop.run_in_executor(None, os.close, stderr_write_fd)

        execution = await self._create_execution(
            process=process,
            stdin=stdin_write_fd,
            stdout=stdout_read_fd,
            stderr=stderr_read_fd,
        )
        
        return execution
    
    async def _create_execution(
        self, 
        process: Process, 
        stdin: FileDescriptor, 
        stdout: FileDescriptor, 
        stderr: FileDescriptor
    ) -> Execution:
        id = len(self._executions) + 1
        execution = Execution(id=id, process=process, stdin=stdin, stdout=stdout, stderr=stderr)
        self._executions[id] = execution
        return execution
        

    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.debug("Executor shutting down, killing all executions")
        await asyncio.gather(
            *[execution.kill(signal=signal.SIGTERM) for execution in self._executions.values()]
        )
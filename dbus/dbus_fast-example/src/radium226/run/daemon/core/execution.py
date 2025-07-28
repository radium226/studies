from asyncio.subprocess import Process
from loguru import logger

from ...shared import FileDescriptor, Signal, ExitCode, ExecutionID


class Execution:

    _id: ExecutionID
    _process: Process
    _stdin: FileDescriptor
    _stdout: FileDescriptor
    _stderr: FileDescriptor


    def __init__(self,
        id: ExecutionID,
        process: Process,
        stdin: FileDescriptor, 
        stdout: FileDescriptor, 
        stderr: FileDescriptor
    ):
        self._id = id
        self._process = process
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
    
    @property
    def id(self) -> ExecutionID:
        return self._id

    @property
    def stdin(self) -> FileDescriptor:
        return self._stdin
    
    @property
    def stdout(self) -> FileDescriptor:
        return self._stdout
    
    @property
    def stderr(self) -> FileDescriptor:
        return self._stderr
    
    async def kill(self, signal: Signal) -> None:
        logger.info(f"Killing execution {self.id} with signal {signal}")
        self._process.send_signal(signal)

    async def wait_for(self) -> ExitCode:
        exit_code = await self._process.wait()
        return exit_code
    
    @property
    def exit_code(self) -> ExitCode | None:
        return self._process.returncode
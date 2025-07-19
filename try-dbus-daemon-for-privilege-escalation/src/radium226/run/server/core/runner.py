from typing import Protocol
import asyncio
from dataclasses import dataclass

from loguru import logger

from .types import RunnerID, RunnerStatus, ExitCode, Command, Signal


class RunHandler(Protocol):
    
    def on_stdout(self, runner: 'Runner', stdout: bytes):
        ...

    def on_stderr(self, runner: 'Runner', stderr: bytes):
        ...

    def on_completed(self, runner: 'Runner', exit_code: ExitCode):
        ...


class DefaultRunHandler(RunHandler):

    def on_stdout(self, runner: 'Runner', stdout: bytes):
        stdout_line = stdout.decode().rstrip()
        logger.info("Runner {runner_id} stdout_line: {stdout_line}", runner_id=runner.id, stdout_line=stdout_line)

    def on_stderr(self, runner: 'Runner', stderr: bytes):
        stderr_line = stderr.decode().rstrip()
        logger.error("Runner {runner_id} stderr_line: {stderr_line}", runner_id=runner.id, stderr_line=stderr_line)

    def on_completed(self, runner: 'Runner', exit_code: ExitCode):
        logger.info("Runner {runner_id} completed with exit code {exit_code}", runner_id=runner.id, exit_code=exit_code)


DEFAULT_RUN_HANDLER = DefaultRunHandler()

class RunControl:

    process: asyncio.subprocess.Process
    task_to_wait_for: asyncio.Task[(None, None, ExitCode)]

    def __init__(self, process: asyncio.subprocess.Process, task_to_wait_for: asyncio.Task[None]):
        self.process = process
        self.task_to_wait_for = task_to_wait_for

    async def kill(self, signal: Signal) -> None:
        logger.debug("Killing process {pid} with signal {signal}", pid=self.process.pid, signal=signal)
        self.process.send_signal(signal)

    async def wait_for(self) -> ExitCode:
        _, _, exit_code = await self.task_to_wait_for
        logger.info("RunControl: Process {pid} completed with exit code {exit_code}", pid=self.process.pid, exit_code=exit_code)
        return exit_code



@dataclass
class Runner():

    id: RunnerID | None
    status: RunnerStatus
    command: Command

    async def run(self, handler: RunHandler | None = None) -> RunControl:
        handler = handler or DEFAULT_RUN_HANDLER

        self.status = RunnerStatus.RUNNING
        logger.debug("Starting to command: {command}", command=self.command)
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.debug("Process started with PID: {pid}", pid=process.pid)

        async def _read_stdout():
            logger.debug("Starting to read stdout...")
            while True:
                line = await process.stdout.readline()
                logger.debug("Reading stdout line: {line}", line=line)
                if not line:
                    break
                handler.on_stdout(self, line)

        async def _read_stderr():
            logger.debug("Starting to read stderr...")
            while True:
                line = await process.stderr.readline()
                logger.debug("Reading stderr line: {line}", line=line)
                if not line:
                    break
                handler.on_stderr(self, line)

        async def _wait_for_completion():
            exit_code = await process.wait()
            handler.on_completed(self, exit_code)
            logger.debug("Process completed with exit code: {exit_code}", exit_code=exit_code)
            self.status = RunnerStatus.COMPLETED
            return exit_code

        return RunControl(
            process, 
            asyncio.gather(
                asyncio.create_task(_read_stdout()),
                asyncio.create_task(_read_stderr()),
                asyncio.create_task(_wait_for_completion()),
            )
        )
from typing import Protocol
import asyncio
from dataclasses import dataclass
import os

from loguru import logger

from ...shared.types import RunnerID, RunnerStatus, ExitCode, RunnerContext, Signal


class RunHandler(Protocol):
    
    def on_stdout(self, runner: 'Runner', stdout: bytes) -> None:
        ...

    def on_stderr(self, runner: 'Runner', stderr: bytes) -> None:
        ...

    def on_completed(self, runner: 'Runner', exit_code: ExitCode) -> None:
        ...


class DefaultRunHandler(RunHandler):

    def on_stdout(self, runner: 'Runner', stdout: bytes) -> None:
        stdout_line = stdout.decode().rstrip()
        logger.info("Runner {runner_id} stdout_line: {stdout_line}", runner_id=runner.id, stdout_line=stdout_line)

    def on_stderr(self, runner: 'Runner', stderr: bytes) -> None:
        stderr_line = stderr.decode().rstrip()
        logger.error("Runner {runner_id} stderr_line: {stderr_line}", runner_id=runner.id, stderr_line=stderr_line)

    def on_completed(self, runner: 'Runner', exit_code: ExitCode) -> None:
        logger.info("Runner {runner_id} completed with exit code {exit_code}", runner_id=runner.id, exit_code=exit_code)


@dataclass
class RunIO():

    stdout_fd: int
    stderr_fd: int


DEFAULT_RUN_HANDLER = DefaultRunHandler()

class RunControl:

    process: asyncio.subprocess.Process
    future_to_wait_for: asyncio.Future[tuple[None, None, ExitCode]]
    io: RunIO

    def __init__(self, 
        process: asyncio.subprocess.Process, 
        future_to_wait_for: asyncio.Future[tuple[None, None, ExitCode]],
        io: RunIO
    ) -> None:
        self.process = process
        self.future_to_wait_for = future_to_wait_for
        self.io = io

    async def kill(self, signal: Signal) -> None:
        logger.debug("Killing process {pid} with signal {signal}", pid=self.process.pid, signal=signal)
        self.process.send_signal(signal)

    async def wait_for(self) -> ExitCode:
        _, _, exit_code = await self.future_to_wait_for
        logger.info("RunControl: Process {pid} completed with exit code {exit_code}", pid=self.process.pid, exit_code=exit_code)
        return exit_code



@dataclass
class Runner():

    id: RunnerID | None
    status: RunnerStatus
    context: RunnerContext

    async def run(self, handler: RunHandler | None = None) -> RunControl:
        try:
            logger.debug("Running command: {command} in directory: {cwd} with env vars: {env}",
                         command=self.context.command,
                         cwd=self.context.working_folder_path,
                         env=list(self.context.environment_variables.keys()))
            handler = handler or DEFAULT_RUN_HANDLER

            stdout_read_fd, stdout_write_fs = os.pipe()
            logger.debug("Created pipe for stdout: read_fd={stdout_read_fd}, write_fd={stdout_write_fs}", stdout_read_fd=stdout_read_fd, stdout_write_fs=stdout_write_fs)
            stderr_read_fd, stderr_write_fs = os.pipe()
            logger.debug("Created pipe for stderr: read_fd={stderr_read_fd}, write_fd={stderr_write_fs}", stderr_read_fd=stderr_read_fd, stderr_write_fs=stderr_write_fs)

            self.status = RunnerStatus.RUNNING
            logger.debug("Starting command: {command} in directory: {cwd} with env vars: {env}", 
                        command=self.context.command, 
                        cwd=self.context.working_folder_path,
                        env=list(self.context.environment_variables.keys()))
            process = await asyncio.create_subprocess_exec(
                *self.context.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.context.working_folder_path,
                env=self.context.environment_variables,
            )
            logger.debug("Process started with PID: {pid}", pid=process.pid)

            async def _read_stdout() -> None:
                logger.debug("Starting to read stdout...")
                while True:
                    if process.stdout is None:
                        break
                    line = await process.stdout.readline()
                    logger.debug("Reading stdout line: {line}", line=line)
                    if not line:
                        break
                    handler.on_stdout(self, line)
                    os.write(stdout_write_fs, line)

            async def _read_stderr() -> None:
                logger.debug("Starting to read stderr...")
                while True:
                    if process.stderr is None:
                        break
                    line = await process.stderr.readline()
                    logger.debug("Reading stderr line: {line}", line=line)
                    if not line:
                        break
                    handler.on_stderr(self, line)
                    os.write(stderr_write_fs, line)

            async def _wait_for_completion() -> ExitCode:
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
                ), 
                RunIO(stdout_fd=stdout_read_fd, stderr_fd=stderr_read_fd)
            )
        except Exception as e:
            logger.error("Error while running command: {command} - {error}", command=self.context.command, error=e)
            self.status = RunnerStatus.FAILED
            raise e
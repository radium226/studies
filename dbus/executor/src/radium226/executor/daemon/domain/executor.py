from asyncio.subprocess import create_subprocess_exec
from asyncio import Future, CancelledError
from loguru import logger
import os

from .execution import Execution


class Executor():

    def __init__(self) -> None:
        super().__init__()
        # Initialize any necessary attributes or state for the executor
        pass


    async def __aenter__(self):
        logger.trace("__aenter__()")
        return self


    async def execute(self, command: list[str], stdin_fd: int, stdout_fd: int, env: dict[str, str], uid: int, cwd: str) -> Execution | None:
        logger.trace("execute({command}, {stdin_fd}, {stdout_fd}, {env}, {uid}, {cwd})", command=command, stdin_fd=stdin_fd, stdout_fd=stdout_fd, env=env, uid=uid, cwd=cwd)        
        
        logger.info("About to execute command: {command} with uid: {uid} in cwd: {cwd}", command=command, uid=uid, cwd=cwd)
        
        def preexec_fn():
            os.setuid(uid)
        
        try:
            process = await create_subprocess_exec(
                *command,
                stdin=stdin_fd,
                stdout=stdout_fd,
                env=env,
                cwd=cwd,
                preexec_fn=preexec_fn,
            )
            execution = Execution(process)
            logger.debug(f"Created Execution with PID: {execution.proces_pid}")
            return execution
        except FileNotFoundError:
            logger.warning(f"The command {command} was not found.", command=command)
            return None

        

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.trace("__aexit__()")
        
        logger.debug("Cleaning up Executor... ")
        logger.debug("Cleaned up! ")


    async def run_forever(self):
        logger.trace("run_forever()")

        logger.debug("Waiting forever... ")
        future = Future()
        try:
            await future
        except CancelledError:
            logger.trace("CancelledError")
        finally:
            logger.debug("Waited! ")
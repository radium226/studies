import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from sdbus import (
    DbusObjectManagerInterfaceAsync, 
    dbus_method_async,
)

from .command_not_found import CommandNotFoundError
from .execution_interface import ExecutionInterface

if TYPE_CHECKING:
    from ...daemon.domain.executor import Executor



class ExecutorInterface(
    DbusObjectManagerInterfaceAsync,
    interface_name="radium226.Executor",
):
    
    _executor: "Executor"

    def __init__(self, executor: "Executor") -> None:
        super().__init__()
        self._executor = executor
        

    @dbus_method_async(
        input_signature="ashha{ss}us",
        method_name="Execute",
        result_signature="o",
    )
    async def execute(self, command: list[str], stdin_fd: int, stdout_fd: int, env: dict[str, str], uid: int, cwd: str) -> str: 
        logger.trace("execute({command}, {stdin_fd}, {stdout_fd}, {env}, {uid}, {cwd})", command=command, stdin_fd=stdin_fd, stdout_fd=stdout_fd, env=env, uid=uid, cwd=cwd)
        execution = await self._executor.execute(command, stdin_fd, stdout_fd, env, uid, cwd)
        if execution is None:
            raise CommandNotFoundError()
        
        execution_path = f"/radium226/Executor/Execution/{execution.proces_pid}"
        execution_interface = ExecutionInterface(execution)
    
        # TODO: Move this directly to Execution (using __aenter__, AsyncExitStack, etc.)
        async def notify_completion():
            logger.trace("notify_completion()")
            exit_code = await execution.wait_for()
            logger.debug(f"Process {execution.proces_pid} finished with exit code: {exit_code}")
            await execution_interface.exit_code.set_async(("i", exit_code))
            await execution_interface.status.set_async("completed")
        
        asyncio.create_task(notify_completion())

        logger.debug(f"Exporting ExecutionInterface to {execution_path}")
        self.export_with_manager(execution_path, execution_interface)
        logger.debug("Exported! ")

        return execution_path

from pathlib import Path

from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

from ..core import Executor
from ...shared.types import ExecutionContext

from .execution_interface import ExecutionInterface


class ExecutorInterface(ServiceInterface):

    executor: Executor
    message_bus: MessageBus

    
    def __init__(self, executor: Executor, message_bus: MessageBus):
        super().__init__("radium226.run.Executor")
        self.executor = executor
        self.message_bus = message_bus


    @method()
    async def Execute(self, command: "as", user_id: "i", current_working_folder_path: "s", environment_variables: "a{ss}") -> "o":  # type: ignore  # noqa: F821,F722
        context = ExecutionContext(
            command=command,
            user_id=user_id,
            current_working_folder_path=Path(current_working_folder_path),
            environment_variables=dict(environment_variables)
        )
        execution = await self.executor.execute(context)
        
        execution_path = f"/radium226/run/execution/{str(execution.id).replace('-', '_')}"
        execution_interface = ExecutionInterface(execution)
        self.message_bus.export(execution_path, execution_interface)

        return execution_path
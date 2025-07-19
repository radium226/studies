
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

from ..core import RunnerManager
from .runner_interface import RunnerInterface


class ServerInterface(ServiceInterface):

    runner_manager: RunnerManager
    message_bus: MessageBus

    
    def __init__(self, runner_manager: RunnerManager, message_bus: MessageBus):
        super().__init__("radium226.run.Server")
        self.runner_manager = runner_manager
        self.message_bus = message_bus


    @method()
    async def PrepareRunner(self, command: "as") -> "o":  # type: ignore  # noqa: F821,F722
        runner = await self.runner_manager.prepare_runner(command)

        if runner.id is None:
            raise Exception("Runner ID is not set")
        
        runner_path = f"/radium226/run/runner/{runner.id.replace('-', '_')}"
        runner_interface = RunnerInterface(runner)
        self.message_bus.export(runner_path, runner_interface)

        return runner_path
import asyncio

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method, signal

from ..core import Server
from .runner_interface import RunnerInterface


class ServerInterface(ServiceInterface):

    server: Server
    message_bus: MessageBus

    
    def __init__(self, server: Server, message_bus: MessageBus):
        super().__init__("radium226.run.Server")
        self.server = server
        self.message_bus = message_bus


    @method()
    async def PrepareRunner(self, command: "as") -> "o":  # type: ignore  # noqa: F722,F82
        runner = await self.server.prepare_runner(command)

        runner_path = f"/radium226/run/runner/{runner.id.replace('-', '_')}"
        runner_interface = RunnerInterface(runner)
        self.message_bus.export(runner_path, runner_interface)

        return runner_path
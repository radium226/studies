import asyncio

from dbus_fast.service import ServiceInterface, method, signal, dbus_property
from dbus_fast import PropertyAccess

from ..core import Execution


class ExecutionInterface(ServiceInterface):

    execution: Execution

    def __init__(self, execution: Execution) -> None:
        super().__init__("radium226.run.Execution")
        self.execution = execution

    @method()
    async def Kill(self, signal: "i") -> None:  # type: ignore  # noqa: F821
        await self.execution.kill(signal)

    @method()
    async def WaitFor(self) -> "i":  # type: ignore  # noqa: F821
        return await self.execution.wait_for()

    @dbus_property(access=PropertyAccess.READ)
    def ExitCode(self) -> "ai":  # type: ignore  # noqa: F821
        return [self.execution.exit_code] if self.execution.exit_code is not None else []
    
    @dbus_property(access=PropertyAccess.READ)
    def Stdin(self) -> "h":  # type: ignore  # noqa: F821
        return self.execution.stdin
    
    @dbus_property(access=PropertyAccess.READ)
    def Stdout(self) -> "h":  # type: ignore  # noqa: F821
        return self.execution.stdout
    
    @dbus_property(access=PropertyAccess.READ)
    def Stderr(self) -> "h":  # type: ignore  # noqa: F821
        return self.execution.stderr

from typing import TYPE_CHECKING

from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_property_async,
)

from loguru import logger

if TYPE_CHECKING:
    from ...daemon.domain.execution import Execution


class ExecutionInterface(
    DbusInterfaceCommonAsync,
    interface_name="radium226.Execution",
):

    _execution: "Execution"
    
    _status: str = "running"

    _exit_code: tuple[str, str | int] = ("s", "")

    def __init__(self, 
        execution: "Execution",
    ) -> None:
        super().__init__()
        self._execution = execution
    
    @dbus_method_async(
        input_signature="i",
        method_name="SendSignal",
    )
    async def send_signal(self, signal: int) -> None:
        logger.trace(f"send_signal({signal})", sigal=signal)
        await self._execution.send_signal(signal)

    @dbus_property_async(
        property_signature='s',
        property_name="Status",
    )
    def status(self) -> str:
        return self._status
    
    @status.setter_private
    def _set_status(self, value: str) -> None:
        logger.trace("_set_status({value})", value=value)
        self._status = value

    @dbus_property_async(
        property_signature='v',
        property_name="ExitCode",
    )
    def exit_code(self) -> tuple[str, str | int]:
        return self._exit_code
    
    @exit_code.setter_private
    def _set_exit_code(self, value: tuple[str, str | int]) -> None:
        logger.trace("_set_exit_code({value})", value=value)
        self._exit_code = value
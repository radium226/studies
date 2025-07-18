import asyncio

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method, signal

from ..core import Runner, RunHandler


class RunHandlerForSignal(RunHandler):

    runner_interface: "RunnerInterface"

    def __init__(self, runner_interface: "RunnerInterface"):
        self.runner_interface = runner_interface

    def on_stdout(self, runner: Runner, stdout_chunk: bytes):
        self.runner_interface.StdOut(stdout_chunk)

    def on_stderr(self, runner: Runner, stderr_chunk: bytes):
        self.runner_interface.StdErr(stderr_chunk)

    def on_completed(self, runner: Runner, exit_code: int):
        self.runner_interface.Completed(exit_code)


class RunnerInterface(ServiceInterface):

    runner: Runner
    run_task: asyncio.Task[None] | None = None

    def __init__(self, runner: Runner):
        super().__init__("radium226.run.Runner")
        self.runner = runner

    @signal()
    def StdOut(self, stdout_chunk: "ay") -> "ay":
        return stdout_chunk
    
    @signal()
    def StdErr(self, stderr_chunk: "ay") -> "ay":
        return stderr_chunk

    @signal()
    def Completed(self, exit_code: "i") -> "i":  # type: ignore  # noqa: F821
        """Signal emitted when command execution is completed"""
        return exit_code

    @method()
    def Run(self) -> None:
        self.run_task = asyncio.create_task(self.runner.run(RunHandlerForSignal(self)))

    @property
    def Status(self) -> "s":
        return self.runner.status.name

    @method()
    def Abort(self) -> None:
        if self.run_task and not self.run_task.done():
            asyncio.wait_for(self.run_task.cancel(), timeout=None)
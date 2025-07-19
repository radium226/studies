import asyncio

from dbus_fast.service import ServiceInterface, method, signal

from ..core import Runner, RunHandler, RunControl


class RunHandlerForSignal(RunHandler):

    runner_interface: "RunnerInterface"

    def __init__(self, runner_interface: "RunnerInterface") -> None:
        self.runner_interface = runner_interface

    def on_stdout(self, runner: Runner, stdout_chunk: bytes) -> None:
        self.runner_interface.StdOut(stdout_chunk)

    def on_stderr(self, runner: Runner, stderr_chunk: bytes) -> None:
        self.runner_interface.StdErr(stderr_chunk)

    def on_completed(self, runner: Runner, exit_code: int) -> None:
        self.runner_interface.Completed(exit_code)


class RunnerInterface(ServiceInterface):

    runner: Runner
    run_task: asyncio.Task[None] | None = None

    run_control: RunControl | None = None

    def __init__(self, runner: Runner) -> None:
        super().__init__("radium226.run.Runner")
        self.runner = runner

    @signal()
    def StdOut(self, stdout_chunk: "ay") -> "ay":  # type: ignore  # noqa: F821
        return stdout_chunk
    
    @signal()
    def StdErr(self, stderr_chunk: "ay") -> "ay":  # type: ignore  # noqa: F821
        return stderr_chunk

    @signal()
    def Completed(self, exit_code: "i") -> "i":  # type: ignore  # noqa: F821
        """Signal emitted when command execution is completed"""
        return exit_code

    @method()
    async def Run(self) -> None:
        self.run_control = await self.runner.run(RunHandlerForSignal(self))

    @property
    def Status(self) -> "s":  # type: ignore  # noqa: F821
        return self.runner.status.name

    @method()
    async def Kill(self, signal: "i") -> None:  # type: ignore  # noqa: F821
        if self.run_control is None:
            raise Exception("RunControl is not set, cannot kill the process")
        await self.run_control.kill(signal)

    @method()
    async def WaitFor(self) -> "i":  # type: ignore  # noqa: F821
        if self.run_control is None:
            raise Exception("RunControl is not set, cannot wait for the process")
        return await self.run_control.wait_for()
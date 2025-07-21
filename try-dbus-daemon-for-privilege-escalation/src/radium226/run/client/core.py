import asyncio
from asyncio import Future
from loguru import logger
import sys
import os
from pathlib import Path

from dbus_fast.aio import MessageBus

from ..shared.types import Command, ExitCode, Signal, RunnerContext



class RunControl:

    exit_code_future: Future[ExitCode]
    runner_interface: any

    def __init__(self, runner_interface: any, exit_code_future: Future[ExitCode]):
        self.runner_interface = runner_interface
        self.exit_code_future = exit_code_future

    async def wait_for(self) -> ExitCode:
        exit_code = await self.exit_code_future
        logger.info("RunControl: Runner completed with exit code: {exit_code}", exit_code=exit_code)
        return exit_code

    async def kill(self, signal: Signal) -> None:
        logger.info("RunControl: Killing runner with signal: {signal}", signal=signal)
        await self.runner_interface.call_kill(signal)
        


class Client():

    def __init__(self, bus: MessageBus):
        self.bus = bus

    async def run(self, command: Command) -> RunControl:
        # Gather context information
        context = RunnerContext(
            command=command,
            user_id=os.getuid(),
            working_folder_path=Path.cwd(),
            environment_variables=dict(os.environ)
        )

        introspection = await self.bus.introspect("radium226.run", "/radium226/run/RunnerManager")
        proxy_object = self.bus.get_proxy_object("radium226.run", "/radium226/run/RunnerManager", introspection)
        runner_manager_interface = proxy_object.get_interface("radium226.run.RunnerManager")

        runner_path = await runner_manager_interface.call_prepare_runner(
            command,
            context.user_id,
            str(context.working_folder_path),
            context.environment_variables
        )
        logger.info("Runner path obtained: {runner_path}", runner_path=runner_path)


        introspection = await self.bus.introspect("radium226.run", runner_path)
        proxy_object = self.bus.get_proxy_object("radium226.run", runner_path, introspection)
        runner_interface = proxy_object.get_interface("radium226.run.Runner")

        print(dir(runner_interface))

        exit_code_future: Future[ExitCode]  = asyncio.Future()

        def handle_stdout(stdout_chunk: bytes) -> None:
            logger.debug("Runner stdout: {stdout_chunk}", stdout_chunk=stdout_chunk)
            sys.stdout.buffer.write(stdout_chunk)
            sys.stdout.flush()

        def handle_stderr(stderr_chunk: bytes) -> None:
            logger.debug("Runner stderr: {stderr_chunk}", stderr_chunk=stderr_chunk)
            sys.stderr.buffer.write(stderr_chunk)
            sys.stderr.flush()

        def handle_completed(exit_code: ExitCode) -> None:
            logger.info("Runner completed with exit code: {exit_code}", exit_code=exit_code)
            exit_code_future.set_result(exit_code)

        runner_interface.on_std_out(handle_stdout)
        runner_interface.on_std_err(handle_stderr)
        runner_interface.on_completed(handle_completed)

        await runner_interface.call_run()

        await asyncio.sleep(0.5)

        logger.info("stdout_fd={stdout_fd}", stdout_fd=await runner_interface.get_stdout_fd())
        stdout_fd = (await runner_interface.get_stdout_fd()).value
        logger.info("stdout_fd={stdout_fd}", stdout_fd=stdout_fd)
        
        async def _read_stdout():
            loop = asyncio.get_event_loop()
            reader = asyncio.StreamReader()
            transport, protocol = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader),
                os.fdopen(stdout_fd, 'rb')
            )
            try:
                # Read all content
                content = await reader.read()
                return content.decode('utf-8', errors='replace')
            finally:
                transport.close()

        asyncio.create_task(_read_stdout())



        return RunControl(runner_interface, exit_code_future)
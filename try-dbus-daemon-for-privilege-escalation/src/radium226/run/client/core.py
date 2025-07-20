import asyncio
from asyncio import Future
from loguru import logger
import sys
import os
from pathlib import Path

from dbus_fast.aio import MessageBus
from dbus_fast import Message, MessageType

from ..shared.types import Command, ExitCode, Signal, RunnerContext



class RunControl:

    exit_code_future: Future[ExitCode]

    def __init__(self, bus: MessageBus, runner_path: str, exit_code_future: Future[ExitCode]):
        self.bus = bus
        self.runner_path = runner_path
        self.exit_code_future = exit_code_future

    async def wait_for(self) -> ExitCode:
        exit_code = await self.exit_code_future
        logger.info("RunControl: Runner completed with exit code: {exit_code}", exit_code=exit_code)
        return exit_code

    async def kill(self, signal: Signal) -> None:
        logger.info("RunControl: Killing runner with signal: {signal}", signal=signal)
        await self.bus.call(
            Message(
                destination="radium226.run",
                path=self.runner_path,
                interface="radium226.run.Runner",
                member="Kill",
                signature="i",
                body=[signal]
            )
        )
        


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
        
        exit_code_future: Future[ExitCode]  = asyncio.Future()

        def handle_message(message: Message) -> None:
            if message.message_type != MessageType.SIGNAL:
                logger.trace("Received non-signal message: {message}", message=message)
                return
            
            if message.interface != "radium226.run.Runner":
                logger.warning("Received message with unexpected interface: {interface}", interface=message.interface)
                return
            
            match message.member:
                case "StdOut":
                    logger.trace("StdOut signal received: {message}", message=message)
                    stdout_chunk = message.body[0]
                    sys.stdout.buffer.write(stdout_chunk)
                    sys.stdout.flush()
            
                case "StdErr":
                    logger.trace("StdErr signal received: {message}", message=message)
                    stderr_chunk = message.body[0]
                    sys.stderr.buffer.write(stderr_chunk)
                    sys.stderr.flush()
            
                case "Completed":
                    logger.trace("Completed signal received: {message}", message=message)
                    exit_code = message.body[0]
                    exit_code_future.set_result(exit_code)

        
        response_message = await self.bus.call(
            Message(
                destination="radium226.run",
                path="/radium226/run/Server",
                interface="radium226.run.Server",
                member="PrepareRunner",
                signature="asisa{ss}",
                body=[context.command, context.user_id, str(context.working_folder_path), context.environment_variables]
            )
        )

        if response_message is None:
            raise Exception("No response from server")
        
        runner_path = response_message.body[0]
        logger.info("Runner path obtained: {runner_path}", runner_path=runner_path)

        self.bus._add_match_rule(f"type='signal',sender=radium226.run,interface=radium226.run.Runner,path={runner_path}")
        self.bus.add_message_handler(handle_message)


        response_message = await self.bus.call(
            Message(
                destination="radium226.run",
                path=runner_path,
                interface="radium226.run.Runner",
                member="Run",
                signature="",
                body=[]
            )
        )
        if response_message is None or response_message.error_name:
            raise Exception(f"Error calling Run on runner: {response_message.error_name if response_message else 'No response'}")
        logger.info("Runner response: {response_message}", response_message=response_message)

        return RunControl(self.bus, runner_path, exit_code_future)
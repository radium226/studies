import asyncio
import os
import signal
import sys
import uuid
from pathlib import Path

import click
from loguru import logger

from ..ipc import open_server, open_client
from ..protocol import ResponseHandler, Emit, Codec
from .messages import RunProcess, KillProcess, ProcessTerminated, CommandNotFound, ProcessKilled, ProcessStarted, Response, Event

from pydantic import Discriminator, TypeAdapter
from typing import Annotated, Never

_DEFAULT_SOCKET_PATH = Path("/tmp/radium226-studies-ipc.sock")


async def async_noop(*_args: object) -> None:
    pass

_TYPE_ADAPTER = TypeAdapter(
    Annotated[
        RunProcess | KillProcess | ProcessStarted | ProcessTerminated | CommandNotFound | ProcessKilled,
        Discriminator("type"),
    ]
)


def encode(message: RunProcess | KillProcess | Event | Response) -> bytes:
    return message.model_dump_json().encode()


def decode(data: bytes) -> RunProcess | KillProcess | Event | Response:
    return _TYPE_ADAPTER.validate_json(data.decode())


CODEC = Codec[RunProcess | KillProcess, ProcessStarted, ProcessTerminated | CommandNotFound | ProcessKilled](
    encode=encode,
    decode=decode,
)


@click.group()
def app() -> None:
    pass


@app.command("start-server")
@click.option("--socket-path", default=_DEFAULT_SOCKET_PATH, type=click.Path(path_type=Path), show_default=True)
def start_server(socket_path: Path) -> None:
    async def handler(request: RunProcess | KillProcess, fds: list[int], emit: Emit[Event]) -> tuple[ProcessTerminated | CommandNotFound | ProcessKilled, list[int]]:
        match request:
            case RunProcess(id=id, command=command, args=args):
                if len(fds) < 3:
                    raise ValueError(f"Expected 3 file descriptors (stdin, stdout, stderr), got {len(fds)}")

                stdin_fd, stdout_fd, stderr_fd = fds[0], fds[1], fds[2]

                try:
                    process = await asyncio.create_subprocess_exec(
                        command, *args,
                        stdin=stdin_fd,
                        stdout=stdout_fd,
                        stderr=stderr_fd,
                        start_new_session=True,
                    )
                except FileNotFoundError:
                    os.close(stdin_fd)
                    os.close(stdout_fd)
                    os.close(stderr_fd)
                    return CommandNotFound(request_id=id, command=command), []

                await emit(ProcessStarted(pid=process.pid), [])

                os.close(stdin_fd)
                os.close(stdout_fd)
                os.close(stderr_fd)

                await process.wait()

                return ProcessTerminated(request_id=id, exit_code=process.returncode or 0), []

            case KillProcess(id=id, pid=pid, signal=sig):
                logger.info("Killing process group {} with signal {}", pid, signal.Signals(sig).name)
                os.killpg(os.getpgid(pid), sig)
                return ProcessKilled(request_id=id, pid=pid), []

            case _:
                raise ValueError(f"Unknown request: {request}")

    async def run() -> None:
        async with open_server(socket_path, CODEC, handler=handler) as server:
            await server.wait_forever()

    asyncio.run(run())


@app.command("run")
@click.argument("command")
@click.argument("args", nargs=-1)
@click.option("--socket-path", default=_DEFAULT_SOCKET_PATH, type=click.Path(path_type=Path), show_default=True)
def run_process(command: str, args: tuple[str, ...], socket_path: Path) -> None:
    exit_code = 0

    async def run() -> int:
        async with open_client(socket_path, CODEC) as client:
            stdin_fd = os.dup(0)
            stdout_fd = os.dup(1)
            stderr_fd = os.dup(2)

            pid: int | None = None
            pending_signals: list[signal.Signals] = []
            result_exit_code = 0
            loop = asyncio.get_running_loop()

            def _send_kill(target_pid: int, sig: signal.Signals) -> None:
                asyncio.create_task(
                    client.request(
                        KillProcess(id=str(uuid.uuid4()), pid=target_pid, signal=sig),
                        handler=ResponseHandler[Never, ProcessKilled](
                            on_response=async_noop,
                        ),
                        fds=[],
                    )
                )

            def forward_signal(sig: signal.Signals) -> None:
                if pid is not None:
                    _send_kill(pid, sig)
                else:
                    pending_signals.append(sig)

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, forward_signal, sig)

            async def on_event(event: ProcessStarted, fds: list[int]) -> None:
                nonlocal pid
                pid = event.pid
                click.echo(f"[event] Process started with PID {event.pid}", err=True)
                for queued_sig in pending_signals:
                    _send_kill(pid, queued_sig)
                pending_signals.clear()

            async def on_response(response: ProcessTerminated | CommandNotFound, fds: list[int]) -> None:
                nonlocal result_exit_code
                match response:
                    case ProcessTerminated(exit_code=exit_code):
                        click.echo(f"[response] Process terminated with exit code {exit_code}", err=True)
                        result_exit_code = exit_code
                    case CommandNotFound(command=cmd):
                        click.echo(f"[response] Command not found: {cmd}", err=True)
                        result_exit_code = 127

            try:
                await client.request(
                    RunProcess(id=str(uuid.uuid4()), command=command, args=list(args)),
                    handler=ResponseHandler[ProcessStarted, ProcessTerminated | CommandNotFound](
                        on_response=on_response,
                        on_event=on_event,
                    ),
                    fds=[stdin_fd, stdout_fd, stderr_fd],
                )
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)

            return result_exit_code

    exit_code = asyncio.run(run())
    if exit_code != 0:
        sys.exit(exit_code)

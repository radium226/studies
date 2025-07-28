#!/usr/bin/env python3

from csv import reader
from typing import Callable, Coroutine, Any
import sys
import pty
import os
from enum import StrEnum
from dataclasses import dataclass

import signal

from threading import Thread

import asyncio

class Mode(StrEnum):

    TTY = "tty"
    PIPE = "pipe"


@dataclass
class Write():

    data: bytes


@dataclass
class Abort():
    
    pass


type Action = Write | Abort


@dataclass
class Redirection():

    abort: Callable[[], Coroutine[Any, Any, None]]
    wait_for: Callable[[], Coroutine[Any, Any, None]]


async def redirect(from_fd: int, to_fd: int) -> Redirection:
    loop = asyncio.get_event_loop()
    abort_read_fd, abort_write_fd = os.pipe()

    from_reader = asyncio.streams.StreamReader()
    from_protocol = asyncio.streams.StreamReaderProtocol(from_reader)
    from_transport, _ = await loop.connect_read_pipe(lambda: from_protocol, os.fdopen(from_fd, 'rb'))

    abort_reader = asyncio.streams.StreamReader()
    abort_protocol = asyncio.streams.StreamReaderProtocol(abort_reader)
    abort_transport, _ = await loop.connect_read_pipe(lambda: abort_protocol, os.fdopen(abort_read_fd, 'rb'))

    to_transport, to_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, 
        os.fdopen(to_fd, 'wb')
    )
    to_writer = asyncio.StreamWriter(to_transport, to_protocol, None, loop)

    async def wait_for_write() -> Write:
        try:
            data = await from_reader.read(1024)
            return Write(data)
        except asyncio.CancelledError:
            print("Redirection cancelled, stopping...")
    
    async def wait_for_abort() -> Abort:
        try:
            toto = await abort_reader.read(1024)
            print(f"Abort signal received: {toto}")  # noqa: T201
            return Abort()
        except asyncio.CancelledError:
            print("Abort wait cancelled, stopping...")
    
    async def wait_for_action() -> Action:
        done, pending = await asyncio.wait(
            [asyncio.create_task(wait_for_write()), asyncio.create_task(wait_for_abort())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        print(f"Done tasks: {done}")
        print(f"Pending tasks: {pending}")  # noqa: T201
        gather = asyncio.gather(*pending)
        gather.cancel()
        try:
            await gather
        except asyncio.CancelledError:
            print("Pending tasks were cancelled, continuing...")  # noqa: T201
            pass
        action = done.pop().result()
        print(f"Action received: {action}")  # noqa: T201
        return action


    async def transfer():
        while True:
            action = await wait_for_action()
            print(f"Processing action: {action}")  # noqa: T201
            match action:
                case Write(data):
                    if not data:
                        print("EOF reached, closing stdin.")
                        break  # EOF
                    print(f"Redirecting stdin (data={data})")
                    try:
                        to_writer.write(data)
                        await to_writer.drain()
                    except OSError as e:
                        print(f"Error writing to stdin: {e}")
                        break
                case Abort():
                    print("Abort signal received, stopping redirection.")
                    break

        # abort_transport.close()
        to_transport.close()
        from_transport.close()
        # os.close(to_fd)

    transfer_task = asyncio.create_task(transfer())

    async def wait_for():
        print("Waiting for transfer to finish...")
        if not transfer_task.done():
            await asyncio.wait([transfer_task])

    async def abort():
        print("Aborting redirection...")
        await asyncio.get_event_loop().run_in_executor(None, os.write, abort_write_fd, b"abort")
        print("Abort signal sent, closing abort_write_fd...")  # noqa: T201
        # abort_transport.close()


    return Redirection(abort=abort, wait_for=wait_for)



async def app(mode: Mode):
    print(f"Running in {mode} mode... ")

    match mode:
        case Mode.TTY:
            stdin_read_fd, stdin_write_fd = pty.openpty()
        case Mode.PIPE:
            stdin_read_fd, stdin_write_fd = os.pipe()
        case _:
            raise ValueError(f"Unknown mode: {mode}")

    print("Starting witness... ")

    def preexec(): # Don't forward signals.
        os.setpgrp()

    process = await asyncio.create_subprocess_exec(
        *["./witness.py"],  # Example command, replace with actual witness command
        stdin=stdin_read_fd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        preexec_fn=preexec,
    )
    os.close(stdin_read_fd)  # Close the read end in the parent process

    
    stdin_redirection = await redirect(sys.stdin.fileno(), stdin_write_fd)
    

    def sigint_signal_handler():
        print(f"Received signal SIGINT, terminating witness...")
        process.send_signal(signal.SIGTERM)

    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, sigint_signal_handler)

    exit_code = await process.wait()
    print(f"Witness finished with exit code: {exit_code}")

    print("Aborting...")
    await stdin_redirection.abort()
    
    print("Waiting for redirection to finish...")
    await stdin_redirection.wait_for()
    
    exit_code = 128 - exit_code
    return exit_code



if __name__ == "__main__":
    mode = Mode(sys.argv[1].lstrip("-")) if len(sys.argv) > 1 else Mode.PIPE
    exit_code = asyncio.run(app(mode))
    sys.exit(exit_code)
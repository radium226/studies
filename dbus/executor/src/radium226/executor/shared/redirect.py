from typing import Callable, Coroutine, Any
import os
from enum import StrEnum
from dataclasses import dataclass
import asyncio
from loguru import logger



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
            logger.debug("Redirection cancelled, stopping...")
        except Exception as e:
            return Abort()
    
    async def wait_for_abort() -> Abort:
        try:
            toto = await abort_reader.read(1024)
            logger.debug(f"Abort signal received: {toto}")  # noqa: T201
            return Abort()
        except asyncio.CancelledError:
            logger.debug("Abort wait cancelled, stopping...")
    
    async def wait_for_action() -> Action:
        done, pending = await asyncio.wait(
            [asyncio.create_task(wait_for_write()), asyncio.create_task(wait_for_abort())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        logger.debug(f"Done tasks: {done}")
        logger.debug(f"Pending tasks: {pending}")  # noqa: T201
        gather = asyncio.gather(*pending)
        gather.cancel()
        try:
            await gather
        except asyncio.CancelledError:
            logger.debug("Pending tasks were cancelled, continuing...")  # noqa: T201
            pass
        action = done.pop().result()
        logger.debug(f"Action received: {action}")  # noqa: T201
        return action


    async def transfer():
        while True:
            action = await wait_for_action()
            logger.debug(f"Processing action: {action}")  # noqa: T201
            match action:
                case Write(data):
                    if not data:
                        logger.debug("EOF reached, closing stdin.")
                        break  # EOF
                    logger.debug(f"Redirecting stdin (data={data})")
                    try:
                        to_writer.write(data)
                        await to_writer.drain()
                    except OSError as e:
                        logger.debug(f"Error writing to stdin: {e}")
                        break
                case Abort():
                    logger.debug("Abort signal received, stopping redirection.")
                    break

        # abort_transport.close()
        to_transport.close()
        from_transport.close()
        # os.close(to_fd)

    transfer_task = asyncio.create_task(transfer())

    async def wait_for():
        logger.debug("Waiting for transfer to finish...")
        if not transfer_task.done():
            await asyncio.wait([transfer_task])

    async def abort():
        logger.debug("Aborting redirection...")
        await asyncio.get_event_loop().run_in_executor(None, os.write, abort_write_fd, b"abort")
        logger.debug("Abort signal sent, closing abort_write_fd...")  # noqa: T201
        # abort_transport.close()


    return Redirection(abort=abort, wait_for=wait_for)
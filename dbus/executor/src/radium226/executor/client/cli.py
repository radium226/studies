from typing import NoReturn
from functools import partial
import asyncio
import sys
from signal import (
    SIGINT,
    SIGTERM,
)

from click import (
    command,
    argument,
    UNPROCESSED,
)

from loguru import logger

from ..shared.dbus.open_bus import open_bus

from .domain.executor import Executor


@command()
@argument("command", nargs=-1, type=UNPROCESSED)
def app(command) -> NoReturn:
    async def coro() -> None:
        async with open_bus() as bus, Executor.connect_to(bus) as executor:
            execution = await executor.execute(list(command))
            if execution is None:
                logger.error(f"Command not found: {' '.join(command)}")
                return 127

            logger.debug("Registering to signal handler for SIGINT...")
            def handle_sigint_signal(signal: int) -> None:
                logger.info("SIGINT signal received, sending signal to execution...")
                asyncio.create_task(execution.send_signal(signal.SIGINT))

            for signal in [SIGINT, SIGTERM]:
                asyncio.get_event_loop().add_signal_handler(signal, partial(handle_sigint_signal, signal))

            logger.info("Wait for execution to finish...")
            exit_code = await execution.wait_for()
            logger.info(f"Execution finished with exit code: {exit_code}")
            return exit_code

    exit_code = asyncio.run(coro())
    sys.exit(exit_code)
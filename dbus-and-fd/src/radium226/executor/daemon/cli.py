"""CLI for echo0d daemon."""

import asyncio
import sys
from typing import NoReturn

from loguru import logger

from .service import start_service


def app() -> NoReturn:
    async def coro() -> None:
        logger.info("Starting Executor daemon...")
        async with start_service() as wait_for:
            logger.info("Executor daemon started! ")
            await wait_for()
            logger.info("Executor daemon stopped.")
    
    asyncio.run(coro())
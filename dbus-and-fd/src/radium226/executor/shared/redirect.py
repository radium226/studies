import asyncio
import os

from loguru import logger



async def redirect(source_fd: int, target_fd: int) -> None:
    try:
        loop = asyncio.get_event_loop()
        while True:
            data = await loop.run_in_executor(None, os.read, source_fd, 1024)
            logger.debug(f"Redirecting data: {data}")
            if not data:
                break
            await loop.run_in_executor(None, os.write, target_fd, data)
    finally:
        logger.debug("Closing file descriptors after redirect.")
        os.close(source_fd)
        os.close(target_fd)
from asyncio import Queue
from typing import AsyncIterator

from loguru import logger


class Channel[ItemT]:

    def __init__(self, max_size: int = 0, name: str = "channel"):
        self.queue: Queue[ItemT | None] = Queue(max_size)
        self.name = name

    async def send(self, item: ItemT) -> None:
        await self.queue.put(item)
        logger.trace(
            "Channel[{}]: item sent ({} queued)", self.name, self.queue.qsize()
        )

    async def close(self) -> None:
        await self.queue.put(None)
        logger.debug(
            "Channel[{}]: closed ({} items still queued)",
            self.name,
            self.queue.qsize(),
        )

    async def __aiter__(self) -> AsyncIterator[ItemT]:
        while True:
            item = await self.queue.get()
            if item is None:
                logger.debug("Channel[{}]: drained", self.name)
                return
            yield item

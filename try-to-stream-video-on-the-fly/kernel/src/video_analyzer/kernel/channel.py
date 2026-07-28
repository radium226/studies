from asyncio import Queue, QueueShutDown
from typing import AsyncIterator

from loguru import logger


class Channel[ItemT]:

    def __init__(self, max_size: int = 0, name: str = "channel"):
        self.queue: Queue[ItemT] = Queue(max_size)
        self.name = name

    async def send(self, item: ItemT) -> None:
        await self.queue.put(item)
        logger.trace(
            "Channel[{}]: item sent ({} queued)", self.name, self.queue.qsize()
        )

    async def close(self) -> None:
        self.queue.shutdown()

    async def __aiter__(self) -> AsyncIterator[ItemT]:
        while True:
            try:
                item = await self.queue.get()
                yield item
            except QueueShutDown:
                logger.debug("Channel[{}]: drained", self.name)
                return
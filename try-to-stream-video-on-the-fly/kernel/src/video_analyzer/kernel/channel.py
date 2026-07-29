from asyncio import Queue, QueueEmpty, QueueShutDown
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

    def try_recv(self) -> ItemT | None:
        """Take an already-queued item without suspending; None if there is
        nothing queued (or the channel is closed).

        Lets a stage that awaits slow work mid-loop catch up on the backlog
        that piled up while it was busy, instead of draining it one item per
        `async for` iteration.
        """
        try:
            return self.queue.get_nowait()
        except (QueueEmpty, QueueShutDown):
            return None

    def close(self) -> None:
        """End the stream. Idempotent, so every producer can close in a
        `finally` without having to know whether it already did. Items queued
        before the close remain consumable (`Queue.shutdown` without
        `immediate`): iteration only ends once the queue has drained — the
        pipeline's clean end-of-stream flush relies on this."""
        self.queue.shutdown()

    async def __aiter__(self) -> AsyncIterator[ItemT]:
        while True:
            try:
                item = await self.queue.get()
                yield item
            except QueueShutDown:
                logger.debug("Channel[{}]: drained", self.name)
                return
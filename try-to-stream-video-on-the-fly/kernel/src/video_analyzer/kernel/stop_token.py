import asyncio

from loguru import logger


class StopToken:
    """Cooperative, idempotent signal to end `Pipeline.drain()`'s frame-reading
    loop early. Setting it does not cancel anything: `produce_frames` merely
    stops asking `FrameSource.read_frame()` for more and takes the exact same
    end-of-stream path as source exhaustion — every frame already read keeps
    flowing through detection/tracking/interpolation/render to completion."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def request_stop(self) -> None:
        if not self._event.is_set():
            logger.debug("StopToken: stop requested")
        self._event.set()

    @property
    def is_stop_requested(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

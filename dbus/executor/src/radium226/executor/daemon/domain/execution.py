from asyncio.subprocess import Process

from loguru import logger

class Execution():

    _proces: Process

    def __init__(self, process: Process) -> None:
        self._process = process

    @property
    def proces_pid(self) -> int:
        return self._process.pid
    
    async def wait_for(self) -> int:
        logger.trace("wait_for()")
        exit_code = await self._process.wait()
        return exit_code
    
    async def send_signal(self, signal: int) -> None:
        logger.trace(f"send_signal({signal})")
        self._process.send_signal(signal)
    

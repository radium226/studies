import asyncio
from loguru import logger
from contextlib import AsyncExitStack

from .types import RunnerManagerConfig
from ...shared.types import Command, RunnerID, RunnerStatus
from .runner import Runner



class RunnerManager():

    config: RunnerManagerConfig

    _runners: dict[RunnerID, Runner] = {}

    def __init__(self, config: RunnerManagerConfig):
        self.config = config
        self.exit_stack = AsyncExitStack()


    async def _start_cleanup_old_runs_loop(self) -> None:
        try:
            while True:
                logger.debug("Starting to cleanup old runners... ")
                pass
                duration_in_seconds = self.config.duration_between_cleanup_of_old_runs.total_seconds()
                logger.debug("Cleanup of old runners completed! Next in {duration_in_seconds} ", duration_in_seconds=duration_in_seconds)
                
                await asyncio.sleep(duration_in_seconds)
        except asyncio.CancelledError:
            logger.debug("Cleanup old runners loop cancelled! ")


    async def _save_runner(self, runner: Runner) -> Runner:
        logger.debug("Saving runner: {runner}", runner=runner)
        # Here you would implement the actual saving logic, e.g., to a database or file
        runner_id = str(max(map(int, self._runners.keys()), default=0) + 1)
        runner.id = runner_id
        self._runners[runner.id] = runner
        return runner
    

    async def _list_runners(self) -> dict[RunnerID, Runner]:
        logger.debug("Loading runners...")
        # Here you would implement the actual loading logic, e.g., from a database or file
        return self._runners


    async def _lookup_runner(self, runner_id: RunnerID) -> Runner | None:
        logger.debug("Looking up runner with ID: {runner_id}", runner_id=runner_id)
        return self._runners.get(runner_id, None)


    async def _delete_runner(self, runner_id: RunnerID) -> None:
        logger.debug("Deleting runner with ID: {runner_id}", runner_id=runner_id)
        if runner_id in self._runners:
            del self._runners[runner_id]
            logger.debug("Runner deleted successfully.")
        else:
            logger.warning("Runner with ID {runner_id} not found.", runner_id=runner_id)


    async def prepare_runner(self, command: Command) -> Runner:
        logger.debug("Executing command: {command}", command=command)
        # Here you would implement the actual execution logic
        return await self._save_runner(Runner(
            id=None,
            status=RunnerStatus.PREPARED,
            command=command,
        ))


    async def start(self) -> None:
        cleanup_old_runs_loop_task = asyncio.create_task(self._start_cleanup_old_runs_loop())
        self.exit_stack.callback(lambda: cleanup_old_runs_loop_task.cancel())


    async def stop(self) -> None:
        await self.exit_stack.aclose()


    async def __aenter__(self) -> "RunnerManager":
        await self.start()
        return self
    

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        await self.stop()


    async def wait_forever(self) -> None:
        future: asyncio.Future[None] = asyncio.Future()
        try:
            await future
        except asyncio.CancelledError:
            logger.debug("RunnerManager wait forever loop cancelled.")
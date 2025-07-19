import pytest

from pendulum import Duration
from typing import AsyncGenerator

from radium226.run.daemon import RunnerManager, RunnerManagerConfig

@pytest.fixture
async def runner_manager() -> AsyncGenerator[RunnerManager, None]:
    config = RunnerManagerConfig(
        duration_between_cleanup_of_old_runs=Duration(seconds=5)
    )

    async with RunnerManager(config) as manager:
        yield manager
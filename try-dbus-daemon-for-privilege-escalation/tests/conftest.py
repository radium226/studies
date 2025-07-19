import pytest

from pendulum import Duration
from typing import AsyncGenerator

from radium226.run.server import Server, ServerConfig

@pytest.fixture
async def server() -> AsyncGenerator[Server, None]:
    config = ServerConfig(
        duration_between_cleanup_of_old_runs=Duration(seconds=5)
    )

    async with Server(config) as server:
        yield server
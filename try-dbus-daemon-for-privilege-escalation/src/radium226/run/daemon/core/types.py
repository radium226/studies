from dataclasses import dataclass, field
from pendulum import Duration
from typing import Self


@dataclass
class RunnerManagerConfig:
    duration_between_cleanup_of_old_runs: Duration = field(default_factory=lambda: Duration(seconds=10))


@dataclass
class ExecutorConfig():

    pass

    @classmethod
    def default(cls) -> Self:
        return cls()
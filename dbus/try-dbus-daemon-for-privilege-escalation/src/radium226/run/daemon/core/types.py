from dataclasses import dataclass, field
from pendulum import Duration
from typing import Self



@dataclass
class ExecutorConfig():

    pass

    @classmethod
    def default(cls) -> Self:
        return cls()
from dataclasses import dataclass, field
from pendulum import Duration


@dataclass
class ServerConfig:
    duration_between_cleanup_of_old_runs: Duration = field(default_factory=lambda: Duration(seconds=10))
from dataclasses import dataclass, field
from pendulum import Duration
from pathlib import Path
from enum import StrEnum, auto


@dataclass
class ServerConfig():

    duration_between_cleanup_of_old_runs: Duration = field(default_factory=lambda: Duration(seconds=10))


type Arg = str

type Command = list[Arg]

type EnvironmentVariables = dict[str, str]

type UserName = str

type UserID = int

type ExitCode = int


@dataclass
class User:
    id: UserID


@dataclass
class ExecutionContext:
    command: Command
    working_folder_path: Path
    environment_variables: EnvironmentVariables
    user: User


type RunnerID = str

class RunnerStatus(StrEnum):

    PREPARED = auto()
    RUNNING = auto()
    COMPLETED = auto()
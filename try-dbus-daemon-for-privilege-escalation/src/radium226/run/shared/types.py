from enum import StrEnum, auto
from dataclasses import dataclass, field
from pathlib import Path


# Basic types
type Arg = str
type Command = list[Arg]
type ExitCode = int
type Signal = int
type RunnerID = str
type UserID = int
type EnvironmentVariables = dict[str, str]


# Data classes
@dataclass
class RunnerContext:
    command: Command
    user_id: UserID
    working_folder_path: Path
    environment_variables: EnvironmentVariables = field(repr=False)


# Enums
class RunnerStatus(StrEnum):
    PREPARED = auto()
    RUNNING = auto()
    COMPLETED = auto()
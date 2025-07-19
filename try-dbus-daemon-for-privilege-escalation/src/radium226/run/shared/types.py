from enum import StrEnum, auto
from dataclasses import dataclass
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
    environment_variables: EnvironmentVariables


# Enums
class RunnerStatus(StrEnum):
    PREPARED = auto()
    RUNNING = auto()
    COMPLETED = auto()
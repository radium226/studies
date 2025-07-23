from enum import StrEnum, auto
from dataclasses import dataclass, field
from pathlib import Path

import os


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


class ExecutionStatus(StrEnum):
    RUNNING = auto()
    COMPLETED = auto()


@dataclass
class ExecutionContext:
    command: Command
    current_working_folder_path: Path = field(default_factory=lambda: Path.cwd())
    environment_variables: EnvironmentVariables = field(default_factory=lambda: dict(os.environ))
    user_id: UserID = field(default_factory=lambda: os.getuid())

type FileDescriptor = int

type ExecutionID = int
from enum import StrEnum, auto
from dataclasses import dataclass, field
from pathlib import Path
import os


type Arg = str
type Command = list[Arg]
type ExitCode = int
type Signal = int
type UserID = int
type EnvironmentVariables = dict[str, str]


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
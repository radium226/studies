from enum import StrEnum, auto


# Basic types
type Arg = str
type Command = list[Arg]
type ExitCode = int
type Signal = int
type RunnerID = str


# Enums
class RunnerStatus(StrEnum):
    PREPARED = auto()
    RUNNING = auto()
    COMPLETED = auto()
from typing import Annotated, Literal, Never

from pydantic import BaseModel, Discriminator

from ..protocol import Request


class ProcessTerminated(BaseModel):
    request_id: str
    exit_code: int
    type: Literal["process_terminated"] = "process_terminated"


class CommandNotFound(BaseModel):
    request_id: str
    command: str
    type: Literal["command_not_found"] = "command_not_found"


class ProcessStarted(BaseModel):
    pid: int
    type: Literal["process_started"] = "process_started"


class RunProcess(BaseModel, Request[ProcessTerminated | CommandNotFound, ProcessStarted]):
    id: str
    command: str
    args: list[str] = []
    type: Literal["run_process"] = "run_process"


class ProcessKilled(BaseModel):
    request_id: str
    pid: int
    type: Literal["process_killed"] = "process_killed"


class KillProcess(BaseModel, Request[ProcessKilled, Never]):
    id: str
    pid: int
    signal: int
    type: Literal["kill_process"] = "kill_process"


type Response = Annotated[ProcessTerminated | CommandNotFound | ProcessKilled, Discriminator("type")]
type Event = ProcessStarted

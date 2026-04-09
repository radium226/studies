from dataclasses import dataclass, field
from uuid import uuid4, UUID

from click import Context



type TagID = UUID

@dataclass(frozen=True, slots=True)
class Tag[DataT]():
    data: DataT
    id: TagID = field(default_factory=uuid4)





@dataclass(frozen=True, slots=True)
class Context():
    ...



@dataclass(frozen=True, slots=True)
class Point():
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Size():
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class Area():
    center: Point
    size: Size


@dataclass(frozen=True, slots=True)
class Found[DataT]():
    tag: Tag[DataT]
    area: Area


@dataclass(frozen=True, slots=True)
class Lost[DataT]():
    tag: Tag[DataT]


type Result[DataT] = Found[DataT] | Lost[DataT]



def track[DataT](context: Context, areas: list[Area]) -> list[Result[DataT]]:
    ...





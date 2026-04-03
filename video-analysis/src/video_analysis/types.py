from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Callable

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Frame:
    index: int
    timestamp: float
    data: NDArray[np.uint8]


@dataclass(frozen=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int


type Detect[T] = Callable[
    [AsyncIterator[Frame]],
    AsyncGenerator[tuple[T, float, float], None],
]

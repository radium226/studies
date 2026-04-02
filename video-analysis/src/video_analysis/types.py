from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class Frame:
    index: int
    timestamp: float
    data: NDArray[np.uint8]


type AnalyzeFrame = Callable[[Frame], Awaitable[None]]

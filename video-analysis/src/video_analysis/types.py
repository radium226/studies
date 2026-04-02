from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray


@dataclass
class Frame:
    index: int
    timestamp: float
    data: NDArray[np.uint8]


@dataclass
class Rectangle:
    x: int
    y: int
    width: int
    height: int


@dataclass
class Detection[T]:
    meta_data: T
    focus_area: Rectangle
    frame: "Frame" = field(init=False)


@dataclass
class Streak[T]:
    detections: list[Detection[T]]

    def iter_frames(self, focus: bool = True):
        for detection in self.detections:
            if focus:
                r = detection.focus_area
                cropped = detection.frame.data[r.y:r.y + r.height, r.x:r.x + r.width]
                yield Frame(
                    index=detection.frame.index,
                    timestamp=detection.frame.timestamp,
                    data=cropped,
                )
            else:
                yield detection.frame


type DetectStreak[T] = Callable[[Frame], Detection[T] | None]

from dataclasses import dataclass

from .bounding_box import BoundingBox

type Landmark = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Detection:
    bounding_box: BoundingBox
    landmarks: tuple[Landmark, Landmark, Landmark, Landmark, Landmark]
    confidence: float

from dataclasses import dataclass

from .bounding_box import BoundingBox

type Landmark = tuple[float, float]


@dataclass(frozen=True, slots=True)
class FaceLandmarks:
    """The 5 canonical face landmark points, in the standard SCRFD/ArcFace order."""

    left_eye: Landmark
    right_eye: Landmark
    nose: Landmark
    mouth_left: Landmark
    mouth_right: Landmark


@dataclass(frozen=True, slots=True)
class Detection:
    bounding_box: BoundingBox
    landmarks: FaceLandmarks
    confidence: float

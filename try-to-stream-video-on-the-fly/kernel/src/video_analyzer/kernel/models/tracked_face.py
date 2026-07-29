from dataclasses import dataclass
from itertools import chain

from .bounding_box import BoundingBox
from .detection import Detection, FaceLandmarks
from .face import Face


@dataclass(frozen=True, slots=True)
class TrackedFace[FaceEmbeddingT]:
    """A `Face` re-identified across snapshots by a stable `track_id`.

    Implements the `Interpolable` protocol: `to_vector`/`with_vector` flatten and
    rebuild the geometry (bounding box + 5 landmarks) as 14 floats, so a generic
    spline interpolator can fill in-between positions without knowing anything
    about faces — `track_id`, `embedding`, and `confidence` are carried over
    unchanged from `self`, since only geometry is interpolated.
    """

    track_id: int
    face: Face[FaceEmbeddingT]

    def to_vector(self) -> list[float]:
        bounding_box = self.face.detection.bounding_box
        landmarks = self.face.detection.landmarks
        return [
            bounding_box.x,
            bounding_box.y,
            bounding_box.width,
            bounding_box.height,
            *chain.from_iterable(
                (
                    landmarks.left_eye,
                    landmarks.right_eye,
                    landmarks.nose,
                    landmarks.mouth_left,
                    landmarks.mouth_right,
                )
            ),
        ]

    def with_vector(self, vector: list[float]) -> "TrackedFace[FaceEmbeddingT]":
        x, y, width, height, *flat_landmarks = vector
        points = list(zip(flat_landmarks[0::2], flat_landmarks[1::2], strict=True))
        detection = Detection(
            bounding_box=BoundingBox(x=x, y=y, width=width, height=height),
            landmarks=FaceLandmarks(
                left_eye=points[0],
                right_eye=points[1],
                nose=points[2],
                mouth_left=points[3],
                mouth_right=points[4],
            ),
            confidence=self.face.detection.confidence,
        )
        return TrackedFace(
            track_id=self.track_id,
            face=Face(detection=detection, embedding=self.face.embedding),
        )

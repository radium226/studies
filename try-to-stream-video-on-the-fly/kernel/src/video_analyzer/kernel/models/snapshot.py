from dataclasses import dataclass

from .frame import FrameIndex


@dataclass(frozen=True, slots=True)
class Snapshot[FaceRecordT]:
    frame_index: FrameIndex
    faces: list[FaceRecordT]
    # Carried over from the sampled frame's own flag: True when that frame
    # opened a new scene, so downstream stages (tracker reset, interpolation
    # windows) can honor the boundary at detection cadence.
    is_scene_start: bool = False

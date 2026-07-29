from dataclasses import dataclass

from .frame import FrameIndex


@dataclass(frozen=True, slots=True)
class Snapshot[FaceRecordT]:
    frame_index: FrameIndex
    faces: list[FaceRecordT]

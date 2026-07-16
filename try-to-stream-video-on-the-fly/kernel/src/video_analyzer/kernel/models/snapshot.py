from dataclasses import dataclass

from .frame import FrameIndex


@dataclass(frozen=True, slots=True)
class Snapshot[DetectionT]:
    frame_index: FrameIndex
    detections: list[DetectionT]

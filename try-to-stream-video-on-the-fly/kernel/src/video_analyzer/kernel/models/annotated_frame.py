from dataclasses import dataclass

from .frame import Frame
from .snapshot import Snapshot


@dataclass(frozen=True, slots=True)
class AnnotatedFrame[FrameContentT, DetectionT]:
    frame: Frame[FrameContentT]
    detections: list[DetectionT]
    bracket: tuple[Snapshot[DetectionT], Snapshot[DetectionT]] | None
    is_exact: bool
    flushed: bool = False

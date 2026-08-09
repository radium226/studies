from dataclasses import dataclass

from .frame import Frame
from .snapshot import Snapshot


@dataclass(frozen=True, slots=True)
class AnnotatedFrame[FrameContentT, FaceRecordT]:
    frame: Frame[FrameContentT]
    faces: list[FaceRecordT]
    # The two real-detection snapshots straddling this rendered frame (the
    # interpolation window it was computed from), or None for a *held* frame —
    # one that had no bracketing detections (the flushed tail past the last
    # snapshot at end of stream) and simply reuses the last emitted `faces`.
    interpolation_bracket: tuple[Snapshot[FaceRecordT], Snapshot[FaceRecordT]] | None
    is_exact: bool

from dataclasses import dataclass

from .frame import Frame
from .snapshot import Snapshot


@dataclass(frozen=True, slots=True)
class AnnotatedFrame[FrameContentT, FaceRecordT]:
    frame: Frame[FrameContentT]
    faces: list[FaceRecordT]
    # The two real-detection snapshots straddling this rendered frame (the
    # interpolation window it was computed from), or None before enough
    # snapshots have arrived to bracket anything yet.
    interpolation_bracket: tuple[Snapshot[FaceRecordT], Snapshot[FaceRecordT]] | None
    is_exact: bool

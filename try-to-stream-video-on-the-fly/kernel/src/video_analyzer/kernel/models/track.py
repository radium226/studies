from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Track[FaceEmbeddingT, FrameContentT]:
    id: int
    faces: list[Face[FaceEmbeddingT]]
    frames: list[FrameContentT]
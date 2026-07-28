from dataclasses import dataclass

from .detection import Detection


@dataclass(frozen=True, slots=True)
class Face[FaceEmbeddingT]:
    detection: Detection
    embedding: FaceEmbeddingT

    def with_embedding[NewFaceEmbeddingT](
        self, embedding: NewFaceEmbeddingT
    ) -> "Face[NewFaceEmbeddingT]":
        return Face(detection=self.detection, embedding=embedding)

from dataclasses import dataclass

from .bounding_box import BoundingBox


@dataclass(frozen=True, slots=True)
class Face[FaceEmbeddingT]:
    bounding_box: BoundingBox
    embedding: FaceEmbeddingT

    def with_embedding[NewFaceEmbeddingT](
        self, embedding: NewFaceEmbeddingT
    ) -> "Face[NewFaceEmbeddingT]":
        return Face(bounding_box=self.bounding_box, embedding=embedding)

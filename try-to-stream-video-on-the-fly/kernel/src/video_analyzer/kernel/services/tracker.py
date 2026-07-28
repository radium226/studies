from abc import ABC, abstractmethod

from ..models import Face, TrackedFace


class Tracker[FaceEmbeddingT](ABC):

    @abstractmethod
    async def update(
        self,
        faces: list[Face[FaceEmbeddingT]],
    ) -> list[TrackedFace[FaceEmbeddingT]]:
        raise NotImplementedError()

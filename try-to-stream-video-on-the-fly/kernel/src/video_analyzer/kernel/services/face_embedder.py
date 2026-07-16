from abc import ABC, abstractmethod

from ..models import Face


class FaceEmbedder[FaceEmbeddingT](ABC):

    @abstractmethod
    def embed_faces(
        self,
        faces: list[Face[None]],
    ) -> list[Face[FaceEmbeddingT]]:
        raise NotImplementedError()

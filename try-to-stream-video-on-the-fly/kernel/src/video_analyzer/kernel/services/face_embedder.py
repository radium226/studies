from abc import ABC, abstractmethod

from ..models import Face, Frame


class FaceEmbedder[FrameContentT, FaceEmbeddingT](ABC):

    @abstractmethod
    async def embed_faces(
        self,
        face_batch: list[tuple[Frame[FrameContentT], Face[None]]],
    ) -> list[Face[FaceEmbeddingT]]:
        raise NotImplementedError()

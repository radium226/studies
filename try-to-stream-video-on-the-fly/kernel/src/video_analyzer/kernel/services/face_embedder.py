from abc import ABC, abstractmethod

from ..models import Face, Frame


class FaceEmbedder[FrameContentT, FaceEmbeddingT](ABC):

    @abstractmethod
    async def embed_faces(
        self,
        face_batch: list[tuple[Frame[FrameContentT], Face[None]]],
    ) -> list[Face[FaceEmbeddingT]]:
        """Compute an embedding per detected face, one output per input pair,
        in input order.

        Each face comes with its originating frame — deliberately, since
        alignment/cropping needs the frame's pixels, not just the detection
        geometry. Pairs may span several source frames; implementations are
        free to batch across them (that's the point of the flat list)."""
        raise NotImplementedError()

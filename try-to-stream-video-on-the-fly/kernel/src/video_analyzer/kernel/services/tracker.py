from abc import ABC, abstractmethod

from ..models import Face, TrackedFace


class Tracker[FaceEmbeddingT](ABC):
    """Stateful multi-object tracker: assigns stable `track_id`s to faces
    across successive calls. `update` must be called once per detection
    snapshot, in frame order — implementations keep temporal state (motion
    prediction), so calls cannot be reordered or parallelized."""

    @abstractmethod
    async def update(
        self,
        faces: list[Face[FaceEmbeddingT]],
    ) -> list[TrackedFace[FaceEmbeddingT]]:
        raise NotImplementedError()

    @abstractmethod
    async def reset(self) -> None:
        """Forget all temporal state. Called on a scene cut: identities never
        survive across scenes, so tracks must not be re-associated with faces
        from a different scene."""
        raise NotImplementedError()

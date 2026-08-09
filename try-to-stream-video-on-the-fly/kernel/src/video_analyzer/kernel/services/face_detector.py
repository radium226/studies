from abc import ABC, abstractmethod

from ..models import Face, Frame


class FaceDetector[FrameContentT](ABC):

    @abstractmethod
    async def detect_faces(
        self,
        frame_batch: list[Frame[FrameContentT]],
    ) -> list[list[Face[None]]]:
        """Detect faces on a batch of frames in one pass.

        Returns exactly one list per input frame, in input order (empty list =
        no faces on that frame). The `None` embedding marks these as
        not-yet-embedded — `FaceEmbedder` fills it in later. Implementations
        should treat frame content as read-only and offload real inference off
        the event loop (the method is async for exactly that)."""
        raise NotImplementedError()

from abc import ABC, abstractmethod

from ..models import Face, Frame


class FaceDetector[FrameContentT](ABC):

    @abstractmethod
    def detect_faces(
        self,
        frame_batch: list[Frame[FrameContentT]],
    ) -> list[list[Face[None]]]:
        raise NotImplementedError()

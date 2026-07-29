from abc import ABC, abstractmethod

from ..models import AnnotatedFrame


class FrameBroadcaster[FrameContentT, FaceRecordT](ABC):

    @abstractmethod
    async def broadcast_frame(
        self,
        annotated_frame: AnnotatedFrame[FrameContentT, FaceRecordT],
    ) -> None:
        raise NotImplementedError()

from abc import ABC, abstractmethod

from ..models import AnnotatedFrame


class FrameBroadcaster[FrameContentT, DetectionT](ABC):

    @abstractmethod
    async def broadcast_frame(
        self,
        annotated_frame: AnnotatedFrame[FrameContentT, DetectionT],
    ) -> None:
        raise NotImplementedError()

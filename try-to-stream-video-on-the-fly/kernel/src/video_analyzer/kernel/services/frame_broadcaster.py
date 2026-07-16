from abc import ABC, abstractmethod

from ..models import Frame


class FrameBroadcaster[FrameContentT](ABC):

    @abstractmethod
    def broadcast_frame(self, frame: Frame[FrameContentT]) -> None:
        raise NotImplementedError()

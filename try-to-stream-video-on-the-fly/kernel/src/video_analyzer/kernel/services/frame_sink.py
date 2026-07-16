from abc import ABC, abstractmethod

from ..models import Frame


class FrameSink[FrameContentT](ABC):

    @abstractmethod
    def write_frame(self, frame: Frame[FrameContentT]) -> None:
        raise NotImplementedError()

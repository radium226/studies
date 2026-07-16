from abc import ABC, abstractmethod

from ..models import Frame


class FrameSource[FrameContentT](ABC):

    @abstractmethod
    def read_frame(self) -> Frame[FrameContentT] | None:
        raise NotImplementedError()

from abc import ABC, abstractmethod

from ..models import AnnotatedFrame


class FrameSink[FrameContentT, DetectionT](ABC):

    @abstractmethod
    async def write_frame(
        self,
        annotated_frame: AnnotatedFrame[FrameContentT, DetectionT],
    ) -> None:
        raise NotImplementedError()

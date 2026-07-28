from abc import ABC, abstractmethod

from ..models import Frame


class TrackEmitter[FrameContentT](ABC):

    @abstractmethod
    def emit_track(
        self,
        previous_frame: Frame[FrameContentT],
        current_frame: Frame[FrameContentT],
    ) -> bool:
        raise NotImplementedError()

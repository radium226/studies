from abc import ABC, abstractmethod

from ..models import Frame


class SceneDetector[FrameContentT](ABC):

    @abstractmethod
    def detect_scene_cut(
        self,
        previous_frame: Frame[FrameContentT],
        current_frame: Frame[FrameContentT],
    ) -> bool:
        raise NotImplementedError()

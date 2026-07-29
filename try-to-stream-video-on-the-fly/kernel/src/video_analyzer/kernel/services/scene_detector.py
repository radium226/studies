from abc import ABC, abstractmethod

from ..models import Frame


class SceneDetector[FrameContentT](ABC):
    """Detects hard cuts between consecutive frames. Called by the pipeline's
    producer on every (previous, current) pair — implementations must be cheap
    relative to the video frame rate. A True return marks `current_frame` as a
    scene start, which resets the tracker and fences detection batches and
    interpolation windows at the cut."""

    @abstractmethod
    async def detect_scene_cut(
        self,
        previous_frame: Frame[FrameContentT],
        current_frame: Frame[FrameContentT],
    ) -> bool:
        raise NotImplementedError()

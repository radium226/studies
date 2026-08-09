from abc import ABC, abstractmethod

from ..models import Frame


class FrameSource[FrameContentT](ABC):

    @abstractmethod
    async def read_frame(self) -> Frame[FrameContentT] | None:
        """Return the next frame, or None once the source is exhausted.

        Frame content is treated as **immutable** by the pipeline: the same
        `Frame` object is fanned out to the detection buffer, the frame sink and
        the broadcaster, so a source is free to hand back a zero-copy read-only
        view (as the ffmpeg-backed one does). Consumers that need to mutate —
        drawing overlays, say — must copy first; see `FrameSink.write_frame`.
        """
        raise NotImplementedError()

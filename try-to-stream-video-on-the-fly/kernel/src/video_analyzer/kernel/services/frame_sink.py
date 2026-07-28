from abc import ABC, abstractmethod

from ..models import AnnotatedFrame


class FrameSink[FrameContentT, DetectionT](ABC):

    @abstractmethod
    async def write_frame(
        self,
        annotated_frame: AnnotatedFrame[FrameContentT, DetectionT],
    ) -> None:
        """Consume one rendered frame. The kernel never draws — burning the
        frame's `detections` into its pixels, if that's wanted, is the sink's job.

        Do it on a **copy**: `annotated_frame.frame.content` is shared with the
        detection buffer and with the `FrameBroadcaster` that is handed the same
        object right after this call, and a `FrameSource` may legitimately return
        read-only content. Mutating it in place corrupts the other consumers —
        including letting the detector see burned-in overlays.
        """
        raise NotImplementedError()

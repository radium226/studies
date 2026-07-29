from abc import ABC, abstractmethod

from ..models import AnnotatedFrame


class FrameBroadcaster[FrameContentT, FaceRecordT](ABC):
    """Second consumer of every rendered frame, alongside `FrameSink`: the
    sink takes the video path (pixels out), the broadcaster the *metadata*
    path — detections, tracks, brackets — for whatever else wants them
    (metrics, recording, stop conditions). Receives the same `AnnotatedFrame`
    object the sink just got, so the same copy-before-mutate rule applies
    (see `FrameSink.write_frame`)."""

    @abstractmethod
    async def broadcast_frame(
        self,
        annotated_frame: AnnotatedFrame[FrameContentT, FaceRecordT],
    ) -> None:
        raise NotImplementedError()

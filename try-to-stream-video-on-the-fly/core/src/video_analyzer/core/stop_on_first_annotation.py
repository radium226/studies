"""Decorates a `FrameBroadcaster`: requests an early pipeline stop the first
time an annotated frame carries at least one face record. Wraps the
broadcaster rather than the sink — that's exactly what `FrameBroadcaster`
exists to carry, per its own role in `Pipeline.write_and_broadcast`."""

from __future__ import annotations

from video_analyzer import kernel


class StopOnFirstAnnotation[FrameContentT, FaceRecordT](
    kernel.FrameBroadcaster[FrameContentT, FaceRecordT]
):

    def __init__(
        self,
        wrapped: kernel.FrameBroadcaster[FrameContentT, FaceRecordT],
        stop_token: kernel.StopToken,
    ) -> None:
        self._wrapped = wrapped
        self._stop_token = stop_token

    async def broadcast_frame(
        self, annotated_frame: kernel.AnnotatedFrame[FrameContentT, FaceRecordT]
    ) -> None:
        await self._wrapped.broadcast_frame(annotated_frame)
        if annotated_frame.faces:
            self._stop_token.request_stop()

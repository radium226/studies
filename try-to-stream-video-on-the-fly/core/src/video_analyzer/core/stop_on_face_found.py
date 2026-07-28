"""Decorates a `FrameBroadcaster`: requests an early pipeline stop the first
time an annotated frame carries at least one detection. Wraps the broadcaster
rather than the sink — detection metadata is exactly what `FrameBroadcaster`
exists to carry, per its own role in `Pipeline.render_and_sink`."""

from __future__ import annotations

from video_analyzer import kernel


class StopOnFaceFound[FrameContentT, DetectionT](
    kernel.FrameBroadcaster[FrameContentT, DetectionT]
):

    def __init__(
        self,
        wrapped: kernel.FrameBroadcaster[FrameContentT, DetectionT],
        stop_token: kernel.StopToken,
    ) -> None:
        self._wrapped = wrapped
        self._stop_token = stop_token

    async def broadcast_frame(
        self, annotated_frame: kernel.AnnotatedFrame[FrameContentT, DetectionT]
    ) -> None:
        await self._wrapped.broadcast_frame(annotated_frame)
        if annotated_frame.detections:
            self._stop_token.request_stop()

"""Decorates a `Tracker`: requests an early pipeline stop the moment the
wrapped tracker confirms its first track — stages earlier than an output-side
stop condition would fire, since nothing downstream (interpolation, lookahead)
has to drain first. The stop itself is still graceful per the `StopToken`
contract: frames already read keep flowing through the pipeline."""

from __future__ import annotations

from video_analyzer import kernel


class StopOnFirstTrack[FaceEmbeddingT](kernel.Tracker[FaceEmbeddingT]):

    def __init__(
        self,
        wrapped: kernel.Tracker[FaceEmbeddingT],
        stop_token: kernel.StopToken,
    ) -> None:
        self._wrapped = wrapped
        self._stop_token = stop_token

    async def update(
        self, faces: list[kernel.Face[FaceEmbeddingT]]
    ) -> list[kernel.TrackedFace[FaceEmbeddingT]]:
        tracked = await self._wrapped.update(faces)
        if tracked:
            self._stop_token.request_stop()
        return tracked

    async def reset(self) -> None:
        await self._wrapped.reset()

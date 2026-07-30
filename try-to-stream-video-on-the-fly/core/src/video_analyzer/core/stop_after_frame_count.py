"""Decorates a `FrameSource`: requests an early pipeline stop once a target
number of frames has been read. This needs no numpy/cv2 — it lives here rather
than in `kernel` only because `kernel` exposes just the `StopToken` primitive,
never a concrete condition for what should set it (see `kernel/CLAUDE.md`)."""

from __future__ import annotations

from video_analyzer import kernel

from .config import StopAfterFrameCountConfig


class StopAfterFrameCount[FrameContentT](kernel.FrameSource[FrameContentT]):

    def __init__(
        self,
        wrapped: kernel.FrameSource[FrameContentT],
        stop_token: kernel.StopToken,
        *,
        config: StopAfterFrameCountConfig,
    ) -> None:
        self._wrapped = wrapped
        self._stop_token = stop_token
        # Required, not defaulted: picking the number is the whole point of
        # wrapping a source in this.
        self.config = config
        self._read_count = 0

    async def read_frame(self) -> kernel.Frame[FrameContentT] | None:
        frame = await self._wrapped.read_frame()
        if frame is not None:
            self._read_count += 1
            if self._read_count >= self.config.max_frames:
                self._stop_token.request_stop()
        return frame

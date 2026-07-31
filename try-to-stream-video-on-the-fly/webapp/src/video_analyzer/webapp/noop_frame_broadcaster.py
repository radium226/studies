"""`kernel.Pipeline` requires a `FrameBroadcaster`, but `core` has no opinion on transport for
detection metadata (see `core/CLAUDE.md`) and this app doesn't consume it either — the HTTP-facing
`Broadcaster` in `broadcaster.py` is a different, unrelated thing (fMP4 byte fan-out, not CV
metadata)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel


class NoopFrameBroadcaster(
    kernel.FrameBroadcaster[NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]]
):
    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            NDArray[np.uint8], kernel.TrackedFace[NDArray[np.float32]]
        ],
    ) -> None:
        pass

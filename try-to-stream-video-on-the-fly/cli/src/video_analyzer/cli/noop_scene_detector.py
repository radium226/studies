"""`kernel.Pipeline` requires a `SceneDetector`, but nothing calls `detect_scene_cut` in `drain()`
today (see `kernel/CLAUDE.md`) and `core` implements no scene-cut algorithm to back one — this
stub only satisfies the constructor."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel


class NoopSceneDetector(kernel.SceneDetector[NDArray[np.uint8]]):
    async def detect_scene_cut(
        self,
        previous_frame: kernel.Frame[NDArray[np.uint8]],
        current_frame: kernel.Frame[NDArray[np.uint8]],
    ) -> bool:
        return False

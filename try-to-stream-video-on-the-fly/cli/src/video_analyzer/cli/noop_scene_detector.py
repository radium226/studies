"""A `SceneDetector` that never reports a cut — the `--no-scene-detection`
backend. The pipeline calls `detect_scene_cut` on every consecutive frame
pair; answering False throughout means tracking and interpolation run
straight through hard cuts, exactly the pre-wiring behavior."""

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

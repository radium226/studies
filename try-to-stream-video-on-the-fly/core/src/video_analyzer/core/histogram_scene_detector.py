"""Histogram-correlation scene-cut detection.

Backs `kernel.SceneDetector` for BGR24 frames: a hard cut changes the global
color distribution abruptly, so consecutive frames whose per-channel
histograms correlate poorly are treated as belonging to different scenes.
Deliberately coarse — it's comparing two histograms per call, cheap enough to
run inline on the event loop at video frame rate (the pipeline calls it on
every consecutive pair), and insensitive to motion, which shifts pixels
around without changing the overall distribution much.
"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel

_DEFAULT_CORRELATION_THRESHOLD = 0.5
_HISTOGRAM_BINS = 32


class HistogramSceneDetector(kernel.SceneDetector[NDArray[np.uint8]]):
    def __init__(
        self, correlation_threshold: float = _DEFAULT_CORRELATION_THRESHOLD
    ) -> None:
        if not 0.0 <= correlation_threshold <= 1.0:
            raise ValueError(
                f"correlation_threshold must be within [0, 1], got {correlation_threshold}"
            )
        self._correlation_threshold = correlation_threshold

    async def detect_scene_cut(
        self,
        previous_frame: kernel.Frame[NDArray[np.uint8]],
        current_frame: kernel.Frame[NDArray[np.uint8]],
    ) -> bool:
        correlation = self._correlation(previous_frame.content, current_frame.content)
        return correlation < self._correlation_threshold

    @staticmethod
    def _correlation(
        previous_bgr: NDArray[np.uint8], current_bgr: NDArray[np.uint8]
    ) -> float:
        # Mean of the three per-channel histogram correlations, each in
        # [-1, 1]: identical content scores ~1, unrelated content ~0 or below.
        channel_correlations: list[float] = []
        for channel in range(3):
            previous_hist = cv2.calcHist(
                [previous_bgr], [channel], None, [_HISTOGRAM_BINS], [0, 256]
            )
            current_hist = cv2.calcHist(
                [current_bgr], [channel], None, [_HISTOGRAM_BINS], [0, 256]
            )
            channel_correlations.append(
                float(cv2.compareHist(previous_hist, current_hist, cv2.HISTCMP_CORREL))
            )
        return sum(channel_correlations) / len(channel_correlations)

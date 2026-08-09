"""HistogramSceneDetector: synthetic-frame sanity checks — identical content
is never a cut, an abrupt global change is."""

import numpy as np
import pytest
from numpy.typing import NDArray

from video_analyzer import kernel
from video_analyzer.core import HistogramSceneDetector, HistogramSceneDetectorConfig


def _frame(index: int, content: NDArray[np.uint8]) -> kernel.Frame[NDArray[np.uint8]]:
    return kernel.Frame(index=index, content=content)


def _noise(seed: int) -> NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(0, 256, (48, 64, 3), dtype=np.uint8)


async def test_identical_frames_are_not_a_cut() -> None:
    detector = HistogramSceneDetector()
    content = _noise(seed=1)
    assert not await detector.detect_scene_cut(_frame(0, content), _frame(1, content))


async def test_small_shift_is_not_a_cut() -> None:
    detector = HistogramSceneDetector()
    content = _noise(seed=1)
    shifted = np.roll(content, 3, axis=1)
    assert not await detector.detect_scene_cut(_frame(0, content), _frame(1, shifted))


async def test_abrupt_content_change_is_a_cut() -> None:
    detector = HistogramSceneDetector()
    dark = np.full((48, 64, 3), 10, dtype=np.uint8)
    bright = np.full((48, 64, 3), 245, dtype=np.uint8)
    assert await detector.detect_scene_cut(_frame(0, dark), _frame(1, bright))


def test_rejects_out_of_range_threshold() -> None:
    with pytest.raises(ValueError):
        HistogramSceneDetectorConfig(correlation_threshold=1.5)

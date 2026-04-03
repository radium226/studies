from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

import numpy as np

from video_analysis.types import Detect, Frame


@dataclass(frozen=True)
class SceneInfo:
    index: int


def _compute_histogram(frame: Frame) -> np.ndarray:
    data = frame.data
    if data.ndim == 3:
        gray = data.mean(axis=2).astype(np.uint8)
    else:
        gray = data
    hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 256))
    return hist.astype(np.float64)


def _correlate(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def scenes_detector(threshold: float = 0.5) -> Detect[SceneInfo]:
    async def detect(
        frames: AsyncIterator[Frame],
    ) -> AsyncGenerator[tuple[SceneInfo, float, float], None]:
        scene_index = 0
        start_ts = 0.0
        prev_ts = 0.0
        prev_hist: np.ndarray | None = None

        async for frame in frames:
            hist = _compute_histogram(frame)
            if prev_hist is not None and _correlate(hist, prev_hist) < threshold:
                yield SceneInfo(index=scene_index), start_ts, prev_ts
                scene_index += 1
                start_ts = frame.timestamp
            prev_hist = hist
            prev_ts = frame.timestamp

        if prev_hist is not None:
            yield SceneInfo(index=scene_index), start_ts, prev_ts

    return detect

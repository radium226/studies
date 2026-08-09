"""PCHIP/cubic/linear spline point query, generic over anything `Interpolable`.

This is the pure numeric half of the render path: the lookahead/segment/
windowing bookkeeping (when to interpolate, which snapshots bracket the query
point) lives in `kernel`'s `RenderCursor`; this class only evaluates one
position of an already-built `list[Interpolable | None]` window, via
`to_vector()`/`with_vector()` — it knows nothing about faces, tracks, or
frames.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline
from scipy.interpolate import PchipInterpolator as _ScipyPchipInterpolator

from video_analyzer import kernel

from .config import InterpolationMethod, SplineInterpolatorConfig


def _interpolate_1d(
    known_indices: NDArray[np.float64],
    known_values: NDArray[np.float64],
    query_index: float,
    method: InterpolationMethod,
) -> NDArray[np.float64]:
    if method == "pchip":
        return _ScipyPchipInterpolator(known_indices, known_values)(query_index)
    if method == "linear":
        return np.array(
            [
                np.interp(query_index, known_indices, known_values[:, dimension])
                for dimension in range(known_values.shape[1])
            ]
        )
    return CubicSpline(known_indices, known_values)(query_index)


class SplineInterpolator[InterpolableT: kernel.Interpolable](
    kernel.Interpolator[InterpolableT]
):
    def __init__(self, *, config: SplineInterpolatorConfig | None = None) -> None:
        self.config = config if config is not None else SplineInterpolatorConfig()

    async def interpolate(
        self,
        interpolables: list[InterpolableT | None],
        at: int,
    ) -> InterpolableT:
        if (known_at := interpolables[at]) is not None:
            return known_at
        known = [
            (index, value) for index, value in enumerate(interpolables) if value is not None
        ]
        if len(known) < 2:
            raise ValueError(
                f"SplineInterpolator.interpolate needs at least 2 known points, got {len(known)}"
            )

        template = known[0][1]
        known_indices = np.array([index for index, _ in known], dtype=np.float64)
        known_values = np.array([value.to_vector() for _, value in known], dtype=np.float64)
        vector = _interpolate_1d(known_indices, known_values, float(at), self.config.method)
        return template.with_vector(list(vector))

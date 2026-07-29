"""PCHIP/cubic/linear spline gap-fill, generic over anything `Interpolable`.

This is the pure numeric half of the pre-kernel `LookaheadTrackBuffer`: the
lookahead/segment/windowing bookkeeping (when to interpolate, which snapshots
bracket the query point) lives in `kernel.Pipeline`'s `_RenderCursor`; this
class only fills numeric gaps in an already-built `list[Interpolable | None]`,
via `to_vector()`/`with_vector()` — it knows nothing about faces, tracks, or
frames.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline
from scipy.interpolate import PchipInterpolator as _ScipyPchipInterpolator

from video_analyzer import kernel

InterpolationMethod = Literal["cubic", "pchip", "linear"]


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
    def __init__(self, method: InterpolationMethod = "pchip") -> None:
        self._method = method

    async def interpolate(
        self,
        interpolables: list[InterpolableT | None],
    ) -> list[InterpolableT]:
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

        result: list[InterpolableT] = []
        for index, value in enumerate(interpolables):
            if value is not None:
                result.append(value)
                continue
            vector = _interpolate_1d(known_indices, known_values, float(index), self._method)
            result.append(template.with_vector(list(vector)))
        return result

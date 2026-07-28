"""PCHIP/cubic/linear spline gap-fill, generic over anything `Interpolable`.

This is the pure numeric half of the pre-kernel `LookaheadTrackBuffer`: the
lookahead/segment/windowing bookkeeping (when to interpolate, which snapshots
bracket the query point) lives in `kernel.Pipeline`'s `_RenderCursor`; this
class only fills numeric gaps in an already-built `list[Interpolable | None]`,
via `to_vector()`/`from_vector()` — it knows nothing about faces, tracks, or
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


def _interp(
    vts: NDArray[np.float64],
    ys: NDArray[np.float64],
    t_q: float,
    method: InterpolationMethod,
) -> NDArray[np.float64]:
    if method == "pchip":
        return _ScipyPchipInterpolator(vts, ys)(t_q)
    if method == "linear":
        return np.array([np.interp(t_q, vts, ys[:, k]) for k in range(ys.shape[1])])
    return CubicSpline(vts, ys)(t_q)


class PchipInterpolator[InterpolableT: kernel.Interpolable](
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
                f"PchipInterpolator.interpolate needs at least 2 known points, got {len(known)}"
            )

        template = known[0][1]
        vts = np.array([index for index, _ in known], dtype=np.float64)
        ys = np.array([value.to_vector() for _, value in known], dtype=np.float64)

        result: list[InterpolableT] = []
        for index, value in enumerate(interpolables):
            if value is not None:
                result.append(value)
                continue
            vector = _interp(vts, ys, float(index), self._method)
            result.append(template.from_vector(list(vector)))
        return result

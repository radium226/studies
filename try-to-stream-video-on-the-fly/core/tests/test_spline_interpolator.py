"""SplineInterpolator: generic point query via Interpolable.to_vector/with_vector,
exercised against a minimal fake — no faces/tracks/frames involved."""

from dataclasses import dataclass

import pytest

from video_analyzer.core.spline_interpolator import SplineInterpolator


@dataclass
class Point:
    value: float

    def to_vector(self) -> list[float]:
        return [self.value]

    def with_vector(self, vector: list[float]) -> "Point":
        return Point(vector[0])


async def test_linear_query_interpolates_midpoint() -> None:
    interpolator = SplineInterpolator(method="linear")
    result = await interpolator.interpolate([Point(0.0), None, Point(2.0)], at=1)
    assert result.value == pytest.approx(1.0)


async def test_known_position_is_returned_unchanged() -> None:
    interpolator = SplineInterpolator(method="linear")
    start, end = Point(5.0), Point(9.0)
    assert (await interpolator.interpolate([start, None, end], at=0)) is start
    assert (await interpolator.interpolate([start, None, end], at=2)) is end


async def test_pchip_query_between_more_than_two_points() -> None:
    interpolator = SplineInterpolator(method="pchip")
    result = await interpolator.interpolate(
        [Point(0.0), Point(1.0), None, Point(1.0)], at=2
    )
    assert result.value == pytest.approx(1.0, abs=0.5)


async def test_raises_with_fewer_than_two_known_points() -> None:
    interpolator = SplineInterpolator(method="linear")
    with pytest.raises(ValueError):
        await interpolator.interpolate([Point(0.0), None, None], at=1)

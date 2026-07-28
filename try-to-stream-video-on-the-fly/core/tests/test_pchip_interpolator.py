"""PchipInterpolator: generic gap-fill via Interpolable.to_vector/from_vector,
exercised against a minimal fake — no faces/tracks/frames involved."""

from dataclasses import dataclass

import pytest

from video_analyzer.core.pchip_interpolator import PchipInterpolator


@dataclass
class Point:
    value: float

    def to_vector(self) -> list[float]:
        return [self.value]

    def from_vector(self, vector: list[float]) -> "Point":
        return Point(vector[0])


async def test_linear_fill_interpolates_midpoint() -> None:
    interpolator = PchipInterpolator(method="linear")
    result = await interpolator.interpolate([Point(0.0), None, Point(2.0)])
    assert result[0].value == 0.0
    assert result[1].value == pytest.approx(1.0)
    assert result[2].value == 2.0


async def test_known_points_are_returned_unchanged() -> None:
    interpolator = PchipInterpolator(method="linear")
    start, end = Point(5.0), Point(9.0)
    result = await interpolator.interpolate([start, None, end])
    assert result[0] is start
    assert result[2] is end


async def test_pchip_fill_between_more_than_two_points() -> None:
    interpolator = PchipInterpolator(method="pchip")
    result = await interpolator.interpolate([Point(0.0), Point(1.0), None, Point(1.0)])
    assert result[2].value == pytest.approx(1.0, abs=0.5)


async def test_raises_with_fewer_than_two_known_points() -> None:
    interpolator = PchipInterpolator(method="linear")
    with pytest.raises(ValueError):
        await interpolator.interpolate([Point(0.0), None, None])

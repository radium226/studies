from abc import ABC, abstractmethod
from typing import Protocol, Self


class Interpolable(Protocol):

    def to_vector(self) -> list[float]: ...

    def with_vector(self, vector: list[float]) -> Self: ...


class Interpolator[InterpolableT: Interpolable](ABC):
    """Stateless numeric gap-fill over a fixed integer grid.

    All *timing* state (which snapshots bracket the query point, when to
    prune) lives in the caller (`RenderCursor`); implementations only do the
    coordinate math via the `Interpolable` protocol.
    """

    @abstractmethod
    async def interpolate(
        self,
        interpolables: list[InterpolableT | None],
        at: int,
    ) -> InterpolableT:
        """Evaluate position `at` of the sequence.

        `interpolables` is a window sampled on consecutive integer positions
        (list index == position); None marks a gap. The caller guarantees at
        least 2 known (non-None) entries and `0 <= at < len(interpolables)`.
        When `interpolables[at]` is already known, return it unchanged — only
        gaps need computing. `at` may lie outside the known points' span;
        implementations choose how to handle that (clamp, extrapolate, ...),
        but the pipeline's caller never asks for it.
        """
        raise NotImplementedError()

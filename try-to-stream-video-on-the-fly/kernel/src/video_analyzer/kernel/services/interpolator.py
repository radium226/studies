from abc import ABC, abstractmethod
from typing import Protocol, Self


class Interpolable(Protocol):

    def to_vector(self) -> list[float]: ...

    def with_vector(self, vector: list[float]) -> Self: ...


class Interpolator[InterpolableT: Interpolable](ABC):

    @abstractmethod
    async def interpolate(
        self,
        interpolables: list[InterpolableT | None],
    ) -> list[InterpolableT]:
        raise NotImplementedError()

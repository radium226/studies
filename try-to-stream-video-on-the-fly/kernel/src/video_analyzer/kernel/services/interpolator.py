from typing import Protocol, Self
from abc import ABC, abstractmethod


class Interpolable(Protocol):
    
    def to_vector(self) -> list[float]: ...

    def from_vector(self, vector: list[float]) -> Self: ...


class Interpolator[InterpolableT: Interpolable](ABC):

    @abstractmethod
    def interpolate(
        self,
        interpolables: list[InterpolableT | None],
    ) -> list[InterpolableT]:
        raise NotImplementedError()
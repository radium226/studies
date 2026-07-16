from abc import ABC, abstractmethod


class Clock(ABC):

    @abstractmethod
    def now(self) -> float:
        raise NotImplementedError()

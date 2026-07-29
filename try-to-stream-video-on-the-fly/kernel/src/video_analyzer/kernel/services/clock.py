from abc import ABC, abstractmethod


class Clock(ABC):

    @abstractmethod
    def now(self) -> float:
        """Current time in seconds, on a **monotonic** scale: consumers
        (`TokenBucket`, `BatchGate`) only ever difference two readings, so the
        epoch is arbitrary but the value must never go backwards — wall-clock
        time (which can jump) is not a valid implementation."""
        raise NotImplementedError()

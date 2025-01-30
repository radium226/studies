from typing import Protocol, runtime_checkable

type ArgName = str


@runtime_checkable
class Export(Protocol):
    
    def refresh(self) -> None:
        ...
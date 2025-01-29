from typing import Protocol

type ArgName = str


class Export(Protocol):
    
    def refresh(self) -> None:
        ...
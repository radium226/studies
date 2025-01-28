from typing import Protocol, Type, Any

type ArgName = str


class Export(Protocol):
    
    def refresh(self) -> None:
        ...
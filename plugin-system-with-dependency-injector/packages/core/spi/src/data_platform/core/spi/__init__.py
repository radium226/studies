from typing import Protocol


class Export(Protocol):
    
    def refresh(self) -> None:
        ...
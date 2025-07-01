from typing import Protocol



class Source[P: str](Protocol):

    def load(
        self,
        full: dict[P, bool] | bool | None = None,
    ) -> None:
        ...
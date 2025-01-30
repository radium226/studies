from typing import ClassVar
from abc import ABC, abstractmethod
from functools import wraps

type ExportName = str
type ExportSchedule = str | None


class Export(ABC):

    _NAME: ClassVar[ExportName]

    _SCHEDULE: ClassVar[ExportSchedule]


    def __init_subclass__(cls, /, name: ExportName, schedule: ExportSchedule = None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._NAME = name
        cls._SCHEDULE = schedule


    @classmethod
    def schedule(cls) -> ExportSchedule:
        return cls._SCHEDULE
    
    @classmethod
    def name(cls) -> ExportName:
        return cls._NAME


    @abstractmethod
    def refresh(self) -> None:
        ...


def export(name: str | None = None):
    def decorator(func):
        @wraps(func)   # In order to preserve docstrings, etc.
        def wrapped(*args, **kwargs):
            return func(*args, **kwargs)
        
        class InlineExport(Export, name=name or func.__name__):

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def refresh(self):
                wrapped(*self.args, **self.kwargs)

            def __call__(self):
                self.refresh()

        return InlineExport

    return decorator

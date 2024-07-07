from typing import Protocol, TypeAlias, NamedTuple, TypeVar, Generic, Type, Any
from click import Group



SourceName: TypeAlias = str

EntityName: TypeAlias = str


CLI = NamedTuple("CLI", [("group", Group)])


Config: TypeAlias = dict[str, Any]


class Source(Protocol):
    
    def __init__(self, config: Config):
        ...

    def refresh(self, entity_names: list[EntityName]):
        ...


S = TypeVar("S", bound=Source, covariant=True)


class SourceSpec(Generic[S], Protocol):

    def source_class(self) -> Type[S]:
        ...

    def source_name(self) -> SourceName:
        ...

    def entity_names(self) -> list[EntityName]:
        ...

    def cli(self) -> CLI:
        ...

    
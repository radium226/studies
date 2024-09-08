from typing import Protocol, TypeAlias, NamedTuple, TypeVar, Generic, Type, Any
from click import Group
from dataclasses import dataclass



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

C = TypeVar("C", bound=Source, covariant=True)


@dataclass
class SourceSpec(Generic[S]):

    source_class: Type[S]

    config_class: Type[C]

    source_name: SourceName

    entity_names: list[EntityName]
from .spi import Source, SourceName, EntityName, CLI, SourceSpec, Config
from click import command, group, pass_context, option, Context
from typing import Any, Type, TypeAlias
from dataclasses import dataclass


Token: TypeAlias = str


@dataclass
class FooConfig():

    token: Token


class FooSource(Source):

    def __init__(self, config: Config):
        self.config = config

    def refresh(self, entity_names: list[EntityName]):
        token = self.config.token
        print(f"Refreshing {entity_names} from foo using {token}")


SPEC = SourceSpec[FooSource](
    source_class=FooSource,
    config_class=FooConfig,
    source_name="foo",
    entity_names=["fizz", "buzz"]
)
from .spi import Source, SourceName, EntityName, CLI, SourceSpec, Config
from click import command, group, pass_context, option, Context
from typing import Any, Type
from dataclasses import dataclass


class FooSource(Source):

    def __init__(self, config: Config):
        self.config = config

    def name(self) -> SourceName:
        return "foo"

    def entity_names(self) -> list[EntityName]:
        return ["fizz", "buzz"]
    
    def refresh(self, entity_names: list[EntityName]):
        token = self.config.get("token", "DEFAULT")
        print(f"Refreshing {entity_names} from foo using {token}")


@group
@option("-t", "--token", "token")
@pass_context
def _cli_group(context: Context, token: str | None):
    context.obj["foo"] = {}
    if token:
        context.obj["foo"]["token"] = token


SPEC = SourceSpec[FooSource](
    source_class=FooSource,
    source_name="foo",
    entity_names=["fizz", "buzz"],
    cli=CLI(group=_cli_group)
)
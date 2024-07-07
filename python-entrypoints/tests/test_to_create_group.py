from typing import TypeAlias, TypeVar, Type

from dataclasses import dataclass, fields
from click import group, Context, option, command, pass_context, Group

from click.testing import CliRunner


Token: TypeAlias = str

ClientID: TypeAlias = str

ClientSecret: TypeAlias = str


@dataclass
class Config():
    
    client_secret: ClientSecret | None = None

    client_id: ClientID | None = None


T = TypeVar("T")


def create_group(config_class: Type[T]) -> Group:
    
    def func(context: Context, **kwargs):
        context.obj = config_class(**kwargs)

    func = pass_context(func)

    for field in fields(config_class):
        func = option(f"--{field.name}", field.name, type=str, default=field.default)(func)

    func = group(func)
    
    return func


def test_to_create_group():
    foo_group = create_group(Config)

    @command()
    @pass_context
    def bar_command(context: Context):
        print(context.obj)

    foo_group.add_command(bar_command, name="bar")

    @group()
    def app():
        pass

    app.add_command(foo_group, name="foo")

    runner = CliRunner()
    result = runner.invoke(app, ['foo', '--client_id', '123', 'bar'])
    assert result.exit_code == 0
    print(result.output)
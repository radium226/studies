from click import group, command, argument, Choice, option, pass_context, Context

from .data_platform import DataPlatform
from .sources.spi import EntityName, Source, SourceSpec

def create_refresh_command(source_spec: SourceSpec):
    entity_names = source_spec.entity_names

    @command()
    @option("-e", "--entity", "entity_names", multiple=True, type=Choice(entity_names), default=entity_names)
    @pass_context
    def func(context: Context, entity_names: list[EntityName]):
        config = context.obj
        data_platform = DataPlatform(config)
        data_platform.sources[source_spec.source_name].refresh(entity_names)
    return func


def create_app():
    
    @group()
    @pass_context
    def app(context: Context):
        context.obj = {}
        

    @app.group(name="source")
    def parent_source_group():
        pass
    
    for source_spec in DataPlatform.list_source_specs():
        source_group = source_spec.cli.group
        parent_source_group.add_command(source_group, name=source_spec.source_name)

        refresh_command = create_refresh_command(source_spec) 
        source_group.add_command(refresh_command, name="refresh")

    # @app.command()
    # def list_sources():
    #     data_platform = DataPlatform()
    #     for source in data_platform.list_sources():
    #         print(source.name())

    return app


app = create_app()
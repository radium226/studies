from click import command, group
from importlib.metadata import entry_points
from importlib import import_module
from dependency_injector import containers, providers
        

def create_tools_provider():
    obj = {}
    for entry_point in entry_points(group="data_platform.tools"):
        obj[entry_point.name] = providers.Container(
            import_module(entry_point.module).Container
        )

    print(obj)
    
    return providers.Aggregate(**obj)

def create_exports_provider(tools_provider):
    obj = {}
    for entry_point in entry_points(group="data_platform.exports"):
        obj[entry_point.name] = providers.Container(
            import_module(entry_point.module).Container(
                tools=tools_provider
            )
        )

    print(obj)
    
    return providers.Aggregate(**obj)


@command()
def app():
    print("Hello! ")

    tools_provider = create_tools_provider()
    exports_provider = create_exports_provider(tools_provider)

    exports_provider.sales.sales_export().refresh()
    

    # tools_container = containers.DynamicContainer()
    # # register_tools(tools_container)

    # exports_container = containers.DynamicContainer()
    # setattr(exports_container, "tools", providers.Container(create_tool_container))

    # # exports_container = containers.DynamicContainer()
    # # exports_container.tools = tools_container
    # # register_exports(exports_container)

    # for a, b in exports_container.__dict__.items():
    #     print(f"{a}: {b}")

    # #exports_container.sales_export().refresh()

__all__ = ["app"]
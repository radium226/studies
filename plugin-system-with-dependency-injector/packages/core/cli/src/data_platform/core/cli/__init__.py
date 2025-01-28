from click import command, group
from importlib.metadata import entry_points
from importlib import import_module
from types import ModuleType
from typing import Any
import inspect
import networkx as nx

# def create_tools_provider():
#     obj = {}
#     for entry_point in entry_points(group="data_platform.tools"):
#         obj[entry_point.name] = providers.Container(
#             import_module(entry_point.module).Container
#         )

#     print(obj)
    
#     return providers.Aggregate(**obj)

# def create_exports_provider(tools_provider):
#     obj = {}
#     for entry_point in entry_points(group="data_platform.exports"):
#         obj[entry_point.name] = providers.Container(
#             import_module(entry_point.module).Container()
#             )
#         )

#     print(obj)
    
    # return providers.Aggregate(**obj)


def index_modules_by_entry_point_name(group: str) -> dict[str, ModuleType]:
    return {
        entry_point.name: import_module(entry_point.module)
        for entry_point in entry_points(group=group)
    }


def wire_everything(modules_by_entry_point_name: dict[str, ModuleType]) -> dict[str, dict[str | None, Any]]:
    G = nx.DiGraph()

    for module in modules_by_entry_point_name.values():
        wire = module.wire
        for arg_name, factory in wire().items():
            G.add_node(arg_name, factory=factory)

    for module in modules_by_entry_point_name.values():
        wire = module.wire
        for arg_name, factory in wire().items():
            for arg in inspect.signature(factory).parameters.values():
                G.add_edge(arg.name, arg_name)

    context = {}

    for node_name in nx.topological_sort(G):
        factory = G.nodes[node_name]["factory"]
        args = {}
        for arg in inspect.signature(factory).parameters.values():
            args = args | { arg.name: context[arg.name] }
        context = context | { node_name: factory(**args) }

    return context
        

@command()
def app():
    modules_by_entry_point_name = index_modules_by_entry_point_name("data_platform.tools") | index_modules_by_entry_point_name("data_platform.exports")

    context = wire_everything(modules_by_entry_point_name)
    
    context["sales_export"].refresh()


__all__ = ["app"]
from click import command
from importlib.metadata import entry_points
from importlib import import_module
from data_platform.core.di import Module, wire
from data_platform.core.spi import Export


def list_modules_by_name(entry_point_group: str) -> dict[str, Module]:
    return {
        entry_point.name: import_module(entry_point.module).Module
        for entry_point in entry_points(group=entry_point_group)
    }
        

@command()
def app():
    modules_by_name = list_modules_by_name("data_platform.tools") | list_modules_by_name("data_platform.exports")
    pool = wire([module for module in modules_by_name.values()])

    print(pool.objs)

    for name in list_modules_by_name("data_platform.exports").keys():
        export = pool.pick(Export, name=name)
        export.refresh()


__all__ = ["app"]
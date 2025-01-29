from click import command, group
from importlib.metadata import entry_points
from importlib import import_module
from data_platform.core.di import Module, wire
from data_platform.core.spi import Export


def list_modules(entry_point_group: str) -> list[Module]:
    return [
        import_module(entry_point.module).Module
        for entry_point in entry_points(group=entry_point_group)
    ]
        

@command()
def app():
    tools_modules = list_modules("data_platform.tools")
    exports_modules = list_modules("data_platform.exports")

    modules = tools_modules + exports_modules
    pool = wire(modules)

    sales_export = pool.pick(Export, name="sales_export")
    sales_export.refresh()


__all__ = ["app"]
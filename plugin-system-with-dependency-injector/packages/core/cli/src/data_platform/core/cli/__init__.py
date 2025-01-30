from click import command
from importlib.metadata import entry_points
from importlib import import_module
from data_platform.core.di import Module, wire, list_modules
from data_platform.core.spi import Export



        

@command()
def app():
    modules = list_modules()
    pool = wire(modules)

    exports = pool.list(type=Export)
    for export in exports:
        export.refresh()


__all__ = ["app"]
from click import command
from data_platform.core.di import wire, list_modules
from data_platform.core.spi import Export



@command()
def app():
    modules = list_modules()
    pool = wire(modules)

    exports = pool.list(type=Export)
    for export in exports:
        export.refresh()


__all__ = ["app"]
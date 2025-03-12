from click import command
from data_platform.core.di import wire, list_modules, list_givens
from data_platform.core.spi import Export



@command()
def app():
    modules = list_modules()

    export_classes = list_givens(modules, type=Export)
    for export_class in export_classes:
        print(export_class.name())

    pool = wire(modules)
    exports = pool.list(type=Export)
    for export in exports:
        export.refresh()


__all__ = ["app"]
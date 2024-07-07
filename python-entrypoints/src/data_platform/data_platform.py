from typing import Generator

from importlib.metadata import entry_points
from importlib import import_module

from .sources.spi import Source, SourceSpec, Config, SourceName


class DataPlatform():

    sources: dict[SourceName, Source]

    def __init__(self, config: Config):
        self.sources = {}
        self._setup_sources(config)

    def _setup_sources(self, config: Config):
        for source_spec in self.list_source_specs():
            source_name = source_spec.source_name()
            source_class = source_spec.source_class()
            source_config = config[source_name]
            source = source_class(source_config)
            self.sources[source_name] = source

    @classmethod
    def list_source_specs(cls) -> Generator[SourceSpec, None, None]:
        for entry_point in entry_points(group="data_platform.source_specs"):
            # name = entry_point.name
            value = entry_point.value
            [source_spec_module_name, source_spec_class_name] = value.split(":")
            source_spec_module = import_module(source_spec_module_name)
            source_spec_class = getattr(source_spec_module, source_spec_class_name)
            source_spec = source_spec_class()
            yield source_spec
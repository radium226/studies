from pathlib import Path
from typing import Generator
from time import sleep
from dataclasses import dataclass

from prefect import task, Flow, flow, task
from prefect.artifacts import create_progress_artifact, update_progress_artifact

from enum import StrEnum, auto



class SourceName(StrEnum):
    
    BANQUE_POPULAIRE = auto()
    BOURSOBANK = auto()


class Source():

    def __init__(self, name: SourceName):
        self.name = name

    def load(self) -> None:
        self._extract()
        self._transform()
        self._load()

    @task()
    def _extract(self) -> None:
        sleep(1)
        print(f"Extracting data from {self.name}")
    
    @task()
    def _transform(self) -> None:
        sleep(1)
        print(f"Transforming data from {self.name}")

    @task()
    def _load(self) -> None:
        sleep(1)
        print(f"Loading data from {self.name}")

@flow()
def load_source(source_name: SourceName) -> None:
    source = Source(source_name)
    source.load()
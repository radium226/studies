from pathlib import Path
from typing import Generator
from time import sleep
from dataclasses import dataclass

from prefect import task, Flow, flow, task
from prefect.artifacts import create_progress_artifact, update_progress_artifact

from enum import StrEnum, auto



class Bank(StrEnum):
    
    BANQUE_POPULAIRE = auto()
    BOURSOBANK = auto()



@dataclass
class Transaction():

    bank: Bank
    amount: float


@flow()
def extract_transactions(bank: Bank) -> None:
    file_paths = download_files(bank=bank)
    for file_path in file_paths:
        transactions = parse_transactions(file=file_path)
        store_transactions(transactions)


@task()
def download_files(bank: Bank) -> Generator[Path, None, None]:
    artifact_id = create_progress_artifact(
        progress=0.0,
        description="Downloading files")

    for i in range(10):
        update_progress_artifact(artifact_id, progress=float(i) * 10.00)
        sleep(30)
        yield Path(f"file_{i}.csv")

@task()
def parse_transactions(file: Path) -> Generator[Transaction, None, None]:
    sleep(30)
    for i in range(10):
        yield Transaction(bank=Bank.BANQUE_POPULAIRE, amount=float(i) * 10.00)


@task()
def store_transactions(transactions: Generator[Transaction, None, None]) -> None:
    transaction = list[transactions]    

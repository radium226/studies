"""
The Freebox is reached over its HTTP API from the controller, not from the
host: `command()` is just a placeholder and `process()` does the actual work,
see `ovh.py`.
"""

from loguru import logger
from pyinfra.api import FactBase, StringCommand

from radium226.unattended.freebox import Connection, FreeboxClient


class ConnectionStatus(FactBase[Connection]):
    def command(self) -> StringCommand:
        return StringCommand("echo", "freebox")

    def process(self, output: list[str]) -> Connection:
        logger.info("Fact freebox.ConnectionStatus: querying the Freebox")
        return FreeboxClient().login().connection()

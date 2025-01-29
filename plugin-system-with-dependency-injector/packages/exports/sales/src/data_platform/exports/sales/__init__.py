from typing import Any, Callable
from data_platform.core.spi import Export
from data_platform.core.di import Using, Given
from data_platform.tools.slack import Client as SlackClient



class SalesExport(Export):

    def __init__(self, slack_client: SlackClient):
        self.slack_client = slack_client

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")


class Module():

    NAME = "sales"

    slack_client = Using.auto()
    
    sales_export = Given(
        SalesExport,
        slack_client=slack_client
    )


__all__ = [
    "SalesExport",
    "Module",
]
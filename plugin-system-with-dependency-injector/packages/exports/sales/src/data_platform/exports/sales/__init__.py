from typing import Any, Callable
from data_platform.tools.slack import Client as SlackClient
from data_platform.core.spi import Export



class SalesExport(Export):

    def __init__(self, slack_client: SlackClient):
        self.slack_client = slack_client

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")



def wire() -> dict[str, Callable[..., Any]]:
    def _sales_export(slack_client: SlackClient) -> SalesExport:
        return SalesExport(slack_client=slack_client)

    return {
        "sales_export": _sales_export,
    }


__all__ = [
    "SalesExport",
    "wire",
]
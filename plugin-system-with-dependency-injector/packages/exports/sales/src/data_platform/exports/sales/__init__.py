from typing import Any, Callable, Type, TypeVar
from data_platform.tools.slack import Client as SlackClient
from data_platform.core.spi import Export

from data_platform.tools.slack import Client as SlackClient



class Using[T]:

    def __init__(self):
        pass

class Given[T]:

    def __init__(self, key: str):
        ...
        


class SalesExport(Export):

    def __init__(self, slack_client: SlackClient):
        self.slack_client = slack_client

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")


class Wire():

    slack_client: SlackClient = Using(variant="blue")

    sales_export: SalesExport = Given(
        SalesExport,
        slack_client=slack_client
    )




def wire() -> dict[str, Callable[..., Any]]:
    def _sales_export(slack_client: SlackClient, slack_channel: str = provide(key="sales")) -> SalesExport:
        return SalesExport(slack_client=slack_client)

    return {
        "sales_export": _sales_export,
    }


__all__ = [
    "SalesExport",
    "wire",
]
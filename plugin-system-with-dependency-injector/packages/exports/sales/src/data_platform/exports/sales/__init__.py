from dependency_injector import containers, providers

from data_platform.tools.slack import Client as SlackClient
from data_platform.core.spi import Export



class SalesExport(Export):

    def __init__(self, slack_client: SlackClient):
        self.slack_client = slack_client

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")



class Container(containers.DeclarativeContainer):

    tools = providers.DependenciesContainer()

    sales_export = providers.Singleton(
        SalesExport,
        slack_client=tools.slack.client,
    )



__all__ = [
    "Container",
    "SalesExport",
]
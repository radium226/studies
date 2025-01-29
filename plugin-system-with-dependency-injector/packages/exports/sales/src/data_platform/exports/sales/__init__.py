from data_platform.core.spi import Export
from data_platform.core.di import Using, Given

from data_platform.tools.slack import Client as SlackClient
from data_platform.tools.dbt import DBT


class SalesExport(Export):

    def __init__(self, slack_client: SlackClient, dbt: DBT):
        self.slack_client = slack_client
        self.dbt = dbt

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")


class LeadsToCRMExport(Export):
    
        def refresh(self) -> None:
            print("Refreshing leads to CRM export! ")

class Module():

    NAME = "sales"

    slack_client = Using.auto()

    dbt = Using.auto()
    
    sales_export = Given(
        SalesExport,
        slack_client=slack_client,
        dbt=dbt,
    )

    leads_to_crm_export = Given(
        LeadsToCRMExport,
    )


__all__ = [
    "SalesExport",
    "Module",
]
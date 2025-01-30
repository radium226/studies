from data_platform.core.spi import Export, export
from data_platform.core.di import Using, Given

from data_platform.tools.slack import Client as SlackClient
from data_platform.tools.dbt import DBT


class SalesExport(Export, name="sales"):

    def __init__(self, slack_client: SlackClient, dbt: DBT):
        self.slack_client = slack_client
        self.dbt = dbt

    def refresh(self) -> None:
        print("Refreshing sales export! ")
        self.slack_client.send_message("Sales export has been refreshed!")


class LeadsToCRMExport(Export, name="leads_to_crm"):
    
        def refresh(self) -> None:
            print("Refreshing leads to CRM export! ")


@export()
def small_inline_export(dbt: DBT):
    print("Refreshing small inline export! ")
    dbt.analyse()


class Module():

    slack_client = Using.auto()

    dbt = Using.instance_of(type=DBT)
    
    sales_export = Given(
        SalesExport,
        slack_client=slack_client,
        dbt=dbt,
    )

    leads_to_crm_export = Given(
        LeadsToCRMExport,
    )

    small_inline_export = Given(
        small_inline_export,
        dbt=dbt,
    )


__all__ = [
    "SalesExport",
    "Module",
]
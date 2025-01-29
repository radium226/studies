from pathlib import Path

from data_platform.core.di import Using, Given




class DBT():

    def __init__(self, folder_path):
        self.folder_path = folder_path

    def send_message(self, message):
        print(f"Sending message: {message} using {self.bot_token}")

    @property
    def dlt_source(self):
        return None
    
    def analyse(self):
        print(f"Analysing dbt project in {self.folder_path}...")



class Module():

    NAME = "dbt"

    folder_path = Using.value(obj=Path.cwd() / "dbt")

    dbt = Given(
        DBT,
        folder_path=folder_path
    )
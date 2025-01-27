from dependency_injector import containers, providers
from functools import partial
from os import environ

type BotToken = str


class Client():

    def __init__(self, bot_token: BotToken):
        self.bot_token = bot_token

    def send_message(self, message):
        print(f"Sending message: {message} using {self.bot_token}")


class Container(containers.DeclarativeContainer):

    bot_token = providers.Callable(
        partial(environ.get, "SLACK_BOT_TOKEN"),
    )

    client = providers.Singleton(
        Client, 
        bot_token=bot_token,
    )


__all__ = [
    "Container",
    "Client",
]
from typing import Any, Callable
from os import environ
from functools import partial

type BotToken = str


class Client():

    def __init__(self, bot_token: BotToken):
        self.bot_token = bot_token

    def send_message(self, message):
        print(f"Sending message: {message} using {self.bot_token}")



def wire() -> dict[str, Callable[..., Any]]:
    def _slack_client() -> Client:
        return Client(bot_token=environ.get("SLACK_BOT_TOKEN", "XXX"))

    return {
        "slack_client": _slack_client,
    }
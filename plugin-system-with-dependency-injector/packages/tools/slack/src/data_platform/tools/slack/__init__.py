from data_platform.core.di import Using, Given



type BotToken = str



class Client():

    def __init__(self, bot_token: BotToken):
        self.bot_token = bot_token

    def send_message(self, message):
        print(f"Sending message: {message} using {self.bot_token}")



class Module():

    NAME = "slack"

    bot_token = Using.env_var(name="SLACK_BOT_TOKEN")

    client = Given(
        Client,
        bot_token=bot_token
    )
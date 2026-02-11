from click import group, Context, pass_context, option
from loguru import logger
from types import SimpleNamespace
from typing import cast
from dotenv import load_dotenv
import os

from .ai import AI, AskForMoreDetails, Response, Result
from .slack import Message


@group()
@option(
    "--anthropic-api-key", 
    "anthropic_api_key", 
    required=False, 
    type=str,
)
@pass_context
def app(ctx: Context, anthropic_api_key: str | None = None):
    logger.debug("Loading environment variables from .env file...")
    load_dotenv()
    
    anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    assert anthropic_api_key is not None, "Anthropic API key must be provided via CLI or ANTHTROPIC_API_KEY env variable."

    logger.debug("App is starting... ")
    ctx.obj = SimpleNamespace(
        anthropic_api_key=anthropic_api_key,
    )

@app.command()
@pass_context
def run(ctx: Context):
    anthropic_api_key = cast(str, ctx.obj.anthropic_api_key)

    ai = AI(anthropic_api_key)

    messages: list[Message] = []
    while True:
        line = input('\033[92m' + "> " + '\033[0m')
        author, message_content = line.split(":", maxsplit=1)
        author = author.strip()
        
        message_content = message_content.strip()
        message = Message(author=author, content=message_content)
        messages.append(message)

        logger.debug(message.mentions)
        if "bot" in message.mentions:
            result = ai.handle_new_messages(messages)
            match result:
                case Response():
                    print('\033[92m' + "> " + '\033[0m' + ' ' + '\033[94m' + f"bot: {result.content}" + '\033[0m')

                case AskForMoreDetails():
                    print('\033[93m' + f"bot needs more details: {result.cause}" + '\033[0m')
            
            messages = []    
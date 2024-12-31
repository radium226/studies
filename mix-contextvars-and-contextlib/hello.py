from typing import Generator, TypeAlias, ClassVar, Literal
from contextvars import ContextVar
from contextlib import contextmanager, ExitStack, nullcontext
from random import randint
from dataclasses import dataclass, field
import os
from enum import Enum, auto


NEW_CONVERSATION_NO: int = 0

def new_conversation_no() -> int:
    global NEW_CONVERSATION_NO
    NEW_CONVERSATION_NO += 1
    return NEW_CONVERSATION_NO


@dataclass
class Conversation():

    no: int = field(default_factory=new_conversation_no)


@dataclass
class Context():

    conversation: Conversation | None = None
    

CONTEXT: ContextVar[Context] = ContextVar("Context", default=Context(conversation=None))


Message: TypeAlias = str


class Client:

    def __init__(self) -> None:
        pass

    def send_message(self, message: Message) -> None:
        conversaion_no: int | None = None
        if conversation := CONTEXT.get().conversation:
            conversaion_no = conversation.no

        print(f"Writing {message} in conversation {conversaion_no}.")

    @contextmanager
    def new_conversation(self) -> Generator[Context, None, None]:
        context = Context(conversation=Conversation())
        token = CONTEXT.set(context)
        yield context
        CONTEXT.reset(token)


def main():
    client = Client()

    client.send_message("Hello")
    client.send_message("What's")
    client.send_message("Up?")

    with client.new_conversation():
        client.send_message("I")
        with client.new_conversation():
            client.send_message("How")
            client.send_message("Are")
            client.send_message("You?")

        client.send_message("Am")
        client.send_message("Fine")

    client.send_message("Nice")
    client.send_message("To")
    client.send_message("Know! ")



if __name__ == "__main__":
    main()

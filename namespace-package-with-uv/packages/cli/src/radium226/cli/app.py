from radium226.core import say_messages
from click import argument, command

@command()
@argument("name")
def app(name: str):
    say_messages(name)
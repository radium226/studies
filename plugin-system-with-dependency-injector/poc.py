from typing import Type, Generic, overload, Protocol, Any, ClassVar
import inspect
import networkx as nx
import os


type ModuleName = str


class Module(Protocol):

    NAME: ClassVar[ModuleName]


class Injector[T](Protocol):

    ...


class InstanceOf[T](Injector[T]):

    def __init__(self, type: Type[T]):
        self.type = type


class Value[T](Injector[T]):

    def __init__(self, obj: T):
        self.obj = obj


class EnvVar(Injector[str]):

    def __init__(self, name: str):
        self.name = name


class Using[T]():

    injector: Injector[T]

    def __init__(self, injector: Injector[T]):
        self.injector = injector

    @staticmethod
    def instance_of[I](type: Type[I]) -> "Using[I]":
        return Using[I](InstanceOf[I](type))
    
    @staticmethod
    def value[I](obj: I) -> "Using[I]":
        return Using[I](Value[I](obj))
    
    @staticmethod
    def env_var(name: str) -> "Using[str]":
        return Using[str](EnvVar(name))


class Given[T]():

    dependencies: dict[str, Using[Any]]

    def __init__(
        self, 
        type: Type[T],
        **dependencies: Using[Any],
    ):
        self.type = type
        self.dependencies = dependencies



class SlackClient():
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    def send_message(self, message: str):
        print(f"Sending message: {message} using {self.bot_token}")


class LinearClient():
    
    def __init__(self, api_key: str):
        self.api_key = api_key

    def create_ticket(self):
        print(f"Creating ticket using {self.api_key}")


class UseCase():

    def __init__(self, slack_client: SlackClient, linear_client: LinearClient):
        self.slack_client = slack_client
        self.linear_client = linear_client

    def __call__(self):
        self.slack_client.send_message("Starting use case!")
        self.linear_client.create_ticket()


class SlackModule(Module):

    NAME = "slack"

    bot_token = Using.value(obj="bt_ABC")

    client = Given(
        SlackClient,
        bot_token=bot_token
    )


class LinearModule(Module):

    NAME = "linear"

    api_key = Using.env_var(name="LINEAR_API_KEY")

    client = Given(LinearClient, api_key=api_key)


class UseCaseModule(Module):

    NAME = "use_case"

    slack_client = Using.instance_of(SlackClient)

    linear_client = Using.instance_of(LinearClient)

    use_case = Given(
        UseCase,
        slack_client=slack_client,
        linear_client=linear_client
    )


class Context():

    ...


def wire(modules: list[Type[Module]]) -> dict[Type[Any], Any]:
    type Node = Type[Any]

    G = nx.DiGraph()

    for module in modules:
        for _, given in inspect.getmembers(module, lambda x: isinstance(x, Given)):
            G.add_node(given.type, given=given)

    for module in modules:
        for _, given in inspect.getmembers(module, lambda x: isinstance(x, Given)):
            for dependency in given.dependencies.values():
                if isinstance(injector := dependency.injector, InstanceOf):
                    G.add_edge(injector.type, given.type)
                

    context: dict[Type[Any], Any] = {}
    for type in nx.topological_sort(G):
        given = G.nodes[type]["given"]
        args: dict[str, Any] = {}
        for dependency_name, dependency in given.dependencies.items():
            injector = dependency.injector
            match injector:
                case InstanceOf():
                    args = args | { dependency_name: context[injector.type] }
                case Value():
                    args = args | { dependency_name: injector.obj }

                case EnvVar():
                    args = args | { dependency_name: os.environ[injector.name] }
        context = context | { type: given.type(**args) }

    return context



if __name__ == "__main__":
    modules: list[Type[Module]] = [
        SlackModule,
        LinearModule,
        UseCaseModule,
    ]

    context = wire(modules)
    print(context[UseCase]())
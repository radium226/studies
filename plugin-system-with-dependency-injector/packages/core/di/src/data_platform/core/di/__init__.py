from typing import Type, Protocol, Any, cast
import inspect
import networkx as nx
import os
import sys
from importlib.metadata import entry_points
from importlib import import_module


ENTRY_POINT_GROUP = "data_platform.core.di.modules"


class Module(Protocol):
    
    ...


class Wire[T](Protocol):

    ...


class InstanceOf[T](Wire[T]):

    def __init__(self, type: Type[T]):
        self.type = type


class Value[T](Wire[T]):

    def __init__(self, obj: T):
        self.obj = obj


class EnvVar(Wire[str]):

    def __init__(self, name: str):
        self.name = name


class Auto(Wire[Any]):
    
    pass


class Using[T]():

    wire: Wire[T]

    def __init__(self, wire: Wire[T] | None = None):
        self.wire = wire or Auto()

    @staticmethod
    def instance_of[I](type: Type[I]) -> "Using[I]":
        return Using[I](InstanceOf[I](type))
    
    @staticmethod
    def value[I](obj: I) -> "Using[I]":
        return Using[I](Value[I](obj))
    
    @staticmethod
    def env_var(name: str) -> "Using[str]":
        return Using[str](EnvVar(name))
    
    @staticmethod
    def auto() -> "Using[Any]":
        return Using[Any](Auto())


class Given[T]():

    dependencies: dict[str, Using[Any]]

    def __init__(
        self, 
        type: Type[T],
        **dependencies: Using[Any],
    ):
        self.type = type
        self.dependencies = dependencies



class Pool():

    def __init__(self, objs: dict[tuple[Type[Any], str], Any]):
        self.objs = objs

    def pick[T](self, type: Type[T], name: str | None = None) -> T:
        if name:
            obj = { t[1]: obj for t, obj in self.objs.items() }[name]
        else:
            obj = { t[0]: obj for t, obj in self.objs.items() }[type]

        return cast(T, obj)

    def list[T](self, type: Type[T]) -> list[T]:
        return [
            cast(T, obj)
            for t, obj in self.objs.items()
            if issubclass(t[0], type)
        ]


def list_givens[T](modules: list[Module], type: Type[T]) -> list[Type[T]]:
    givens = []
    for module in modules:
        for _, given in inspect.getmembers(module, lambda x: isinstance(x, Given)):
            if issubclass(given.type, type):
                givens.append(given.type)
    return givens


def wire(modules: list[Type[Module]]) -> Pool:
    G = nx.DiGraph()

    for module in modules:
        for name, given in inspect.getmembers(module, lambda x: isinstance(x, Given)):
            G.add_node(given.type, given=given, name=name)

    for module in modules:
        for _, given in inspect.getmembers(module, lambda x: isinstance(x, Given)):
            for dependency_name, dependency in given.dependencies.items():
                wire = dependency.wire
                match wire:
                    case InstanceOf():
                        wire_type = wire.type
                        given_type = given.type
                        G.add_edge(wire_type, given_type)

                    case Auto():
                        wire_type = inspect.getfullargspec(given.type.__init__).annotations[dependency_name]
                        # Fix to handle forward references
                        if isinstance(wire_type, str):
                            wire_type = getattr(sys.modules[__name__], wire_type)
                        given_type = given.type
                        G.add_edge(wire_type, given.type)
    
    pool = Pool({})
    
    for components in nx.connected_components(G.to_undirected()):
        C = G.subgraph(components).copy().to_directed()
        if not nx.is_directed_acyclic_graph(C):
            raise Exception("Cyclic dependency detected! ")

        for type in nx.topological_sort(C): # type: ignore
            given = G.nodes[type]["given"]
            name = G.nodes[type]["name"]
            args: dict[str, Any] = {}
            for dependency_name, dependency in given.dependencies.items():
                wire = dependency.wire
                match wire:
                    case InstanceOf():
                        args = args | { dependency_name: pool.pick(wire.type) }
                    case Value():
                        args = args | { dependency_name: wire.obj }

                    case EnvVar():
                        args = args | { dependency_name: os.environ[wire.name] }

                    case Auto():
                        wire_type = inspect.getfullargspec(given.type.__init__).annotations[dependency_name]
                        args = args | { dependency_name: pool.pick(wire_type) }

            pool = Pool(pool.objs | { (type, name): given.type(**args) })

    return pool


def list_modules() -> list[Module]:
    return [
        import_module(entry_point.module).Module
        for entry_point in entry_points(group=ENTRY_POINT_GROUP)
    ]


__all__ = [
    "Module",
    "Using",
    "Given",
    "wire",
    "list_modules",
    "list_givens",
]
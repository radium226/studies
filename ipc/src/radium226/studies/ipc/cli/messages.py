from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator

from ..protocol import Request


class World(BaseModel):
    request_id: str
    greeting: str
    type: Literal["world"] = "world"


class Foo(BaseModel):
    type: Literal["foo"] = "foo"
    description: str

class Bar(BaseModel):
    type: Literal["bar"] = "bar"
    description: str


class Hello(BaseModel, Request[World, Foo | Bar]):
    id: str
    name: str
    type: Literal["hello"] = "hello"


type Event = Annotated[Foo | Bar, Discriminator("type")]

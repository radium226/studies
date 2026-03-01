from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator


class Hello(BaseModel):
    id: str
    name: str
    type: Literal["hello"] = "hello"


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


type Event = Annotated[Foo | Bar, Discriminator("type")]

type Request = Hello
# type Request = Annotated[Union[Hello], Discriminator("type")]

type Response = World
# type Response = Annotated[Union[World], Discriminator("type")]
from typing import Awaitable, Callable, Generic, Protocol, TypeVar, runtime_checkable
from dataclasses import dataclass

@runtime_checkable
class Request(Protocol):
    """Structural protocol: any object with `id: str` is a request."""
    id: str


@runtime_checkable
class Response(Protocol):
    """Structural protocol: any object with `request_id: str` is a response."""
    request_id: str

MessageT = TypeVar("MessageT")

type Emit[EventT] = Callable[[EventT, list[int]], Awaitable[None]]


type Encode[MessageT] = Callable[[MessageT], bytes]

type Decode[MessageT] = Callable[[bytes], MessageT]


@dataclass
class Codec[RequestT: Request, EventT, ResponseT: Response]():
    encode: Encode[RequestT | EventT | ResponseT]
    decode: Decode[RequestT | EventT | ResponseT]


type OnEvent[EventT] = Callable[[EventT, list[int]], Awaitable[None]]


type OnResponse[ResponseT: Response] = Callable[[ResponseT, list[int]], Awaitable[None]]


@dataclass
class ResponseHandler[EventT,ResponseT: Response]():
    """Data class bundling event and response handlers for a request."""
    on_event: OnEvent[EventT] | None = None
    on_response: OnResponse[ResponseT] | None = None
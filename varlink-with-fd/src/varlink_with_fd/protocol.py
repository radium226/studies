"""Varlink protocol: JSON messages with null-byte framing."""

from typing import Any

from pydantic import BaseModel


class Request(BaseModel):
    """Varlink request message."""

    method: str
    parameters: dict[str, Any] = {}

    def encode(self) -> bytes:
        """Encode request to bytes with null terminator."""
        return self.model_dump_json().encode("utf-8") + b"\0"

    @classmethod
    def decode(cls, data: bytes) -> "Request":
        """Decode request from bytes (without null terminator)."""
        return cls.model_validate_json(data)


class Response(BaseModel):
    """Varlink response message."""

    parameters: dict[str, Any] | None = None
    error: str | None = None

    def encode(self) -> bytes:
        """Encode response to bytes with null terminator."""
        return self.model_dump_json(exclude_none=True).encode("utf-8") + b"\0"

    @classmethod
    def decode(cls, data: bytes) -> "Response":
        """Decode response from bytes (without null terminator)."""
        return cls.model_validate_json(data)


def read_message(buffer: bytes) -> tuple[bytes | None, bytes]:
    """Extract a complete message from buffer.

    Returns (message, remaining_buffer) where message is None if incomplete.
    """
    idx = buffer.find(b"\0")
    if idx == -1:
        return None, buffer
    return buffer[:idx], buffer[idx + 1 :]

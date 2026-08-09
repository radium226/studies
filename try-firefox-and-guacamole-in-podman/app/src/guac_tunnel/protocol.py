"""The Guacamole wire protocol.

Every message is an *instruction*: a comma-separated list of length-prefixed
elements, terminated by a semicolon. The first element is the opcode.

    6.select,3.vnc;
    ^      ^ ^   ^
    |      | |   +-- terminator
    |      | +------ element value
    |      +-------- separator
    +--------------- length of "select"

The one trap worth knowing: **lengths count Unicode characters, not bytes.**
The protocol is defined over a UTF-8 stream, so `2.é;` is a valid instruction
whose value occupies three bytes. Counting bytes instead desynchronises the
parser the moment anything non-ASCII crosses the wire -- a keystroke, a
clipboard paste, a window title.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

# guacd will not send an instruction larger than this, and neither should we.
# Matches guacamole-common's 8192-character instruction limit.
MAX_INSTRUCTION_LENGTH = 8192


class ProtocolError(Exception):
    """Raised when the byte stream cannot be a Guacamole instruction."""


@dataclass(frozen=True, slots=True)
class Instruction:
    """A single Guacamole instruction: an opcode plus its arguments."""

    opcode: str
    args: tuple[str, ...] = ()

    @classmethod
    def of(cls, opcode: str, *args: object) -> Self:
        """Build an instruction, stringifying arguments for convenience."""
        return cls(opcode, tuple(str(arg) for arg in args))

    def encode(self) -> str:
        """Render to the wire format."""
        elements = (self.opcode, *self.args)
        return ",".join(f"{len(element)}.{element}" for element in elements) + ";"

    def __str__(self) -> str:
        return self.encode()


def encode(opcode: str, *args: object) -> str:
    """Shorthand for ``Instruction.of(opcode, *args).encode()``."""
    return Instruction.of(opcode, *args).encode()


class InstructionParser:
    """Incremental parser turning a character stream into instructions.

    guacd speaks over TCP, so instruction boundaries have nothing to do with
    read boundaries: a single ``recv`` may yield half an instruction, or three
    and a half. Feed whatever arrives and take whatever is complete.
    """

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[Instruction]:
        """Add received characters, returning every instruction now complete."""
        self._buffer += chunk
        instructions: list[Instruction] = []
        consumed = 0

        while (end := self._end_of_instruction(consumed)) is not None:
            instructions.append(self._parse(consumed, end))
            consumed = end

        self._discard(consumed)
        return instructions

    def feed_complete(self, chunk: str) -> str:
        """Add received characters, returning only the whole instructions.

        For forwarding rather than inspecting. The browser's tunnel parses each
        WebSocket message on its own -- it keeps no buffer between messages and
        fails the connection outright on one that ends mid-instruction -- so a
        partial tail must be held back rather than passed along.
        """
        self._buffer += chunk
        consumed = 0

        while (end := self._end_of_instruction(consumed)) is not None:
            consumed = end

        complete = self._buffer[:consumed]
        self._discard(consumed)
        return complete

    @property
    def pending(self) -> str:
        """Characters buffered so far that do not yet form an instruction."""
        return self._buffer

    def _discard(self, consumed: int) -> None:
        self._buffer = self._buffer[consumed:]
        if len(self._buffer) > MAX_INSTRUCTION_LENGTH:
            raise ProtocolError(
                f"incomplete instruction exceeds {MAX_INSTRUCTION_LENGTH} characters"
            )

    def _end_of_instruction(self, start: int) -> int | None:
        """Index just past the instruction beginning at ``start``, if complete."""
        cursor = start

        while True:
            separator = self._buffer.find(".", cursor)
            if separator < 0:
                return None

            digits = self._buffer[cursor:separator]
            if not digits.isdigit():
                raise ProtocolError(f"expected an element length, got {digits!r}")

            # The value, plus the single delimiter character that follows it.
            end = separator + 1 + int(digits)
            if end >= len(self._buffer):
                return None

            delimiter = self._buffer[end]
            if delimiter == ";":
                return end + 1
            if delimiter != ",":
                raise ProtocolError(f"expected ',' or ';' after element, got {delimiter!r}")
            cursor = end + 1

    def _parse(self, start: int, end: int) -> Instruction:
        """Turn an already-delimited span of the buffer into an instruction."""
        elements: list[str] = []
        cursor = start

        while cursor < end:
            separator = self._buffer.index(".", cursor)
            value_end = separator + 1 + int(self._buffer[cursor:separator])
            elements.append(self._buffer[separator + 1 : value_end])
            cursor = value_end + 1

        return Instruction(elements[0], tuple(elements[1:]))

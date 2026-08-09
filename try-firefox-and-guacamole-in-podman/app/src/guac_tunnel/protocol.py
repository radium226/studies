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

        while (instruction := self._take_one()) is not None:
            instructions.append(instruction)

        if len(self._buffer) > MAX_INSTRUCTION_LENGTH:
            raise ProtocolError(
                f"incomplete instruction exceeds {MAX_INSTRUCTION_LENGTH} characters"
            )

        return instructions

    @property
    def pending(self) -> str:
        """Characters buffered so far that do not yet form an instruction."""
        return self._buffer

    def _take_one(self) -> Instruction | None:
        """Consume one complete instruction from the buffer, if there is one."""
        elements: list[str] = []
        cursor = 0

        while True:
            separator = self._buffer.find(".", cursor)
            if separator < 0:
                return None

            digits = self._buffer[cursor:separator]
            if not digits.isdigit():
                raise ProtocolError(f"expected an element length, got {digits!r}")

            length = int(digits)
            # The value, plus the single delimiter character that follows it.
            end = separator + 1 + length
            if end >= len(self._buffer):
                return None

            elements.append(self._buffer[separator + 1 : end])
            delimiter = self._buffer[end]
            cursor = end + 1

            if delimiter == ";":
                self._buffer = self._buffer[cursor:]
                return Instruction(elements[0], tuple(elements[1:]))
            if delimiter != ",":
                raise ProtocolError(f"expected ',' or ';' after element, got {delimiter!r}")

"""The guacd connection handshake.

This is the part the Java webapp would normally do for us. The exchange is:

    client ──► select    which protocol (vnc, rdp, ssh, ...)
    guacd  ──► args      the parameter names this protocol accepts, in order
    client ──► size      display geometry
    client ──► audio     mimetypes the client can decode
    client ──► video
    client ──► image
    client ──► timezone  (only from protocol version 1.1.0 onwards)
    client ──► connect   parameter *values*, positionally matching `args`
    guacd  ──► ready     connection id; the session starts here

Two details that are easy to get wrong:

* ``connect`` is positional. The values must line up with the names guacd sent
  in ``args``, including the empty string for anything we do not set. Sorting
  them, or sending only the ones we care about, silently connects to garbage.
* From 1.5.0 guacd puts a protocol *version* in ``args[0]`` (as the pseudo
  parameter name ``VERSION_1_5_0``). We must echo the negotiated version back
  in that same slot rather than treating it as a real parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol as TypingProtocol

from .protocol import Instruction

#: Highest protocol version this tunnel knows how to speak. Matches the
#: vendored guacamole-common-js, so we never negotiate above the browser.
CLIENT_VERSION = (1, 5, 0)

#: Version-shaped pseudo parameter guacd 1.5+ places first in `args`.
_VERSION_PATTERN = re.compile(r"^VERSION_(\d+)_(\d+)_(\d+)$")

#: What the browser can decode. guacd uses these to pick its encodings.
DEFAULT_AUDIO_MIMETYPES = ("audio/L8", "audio/L16")
DEFAULT_VIDEO_MIMETYPES: tuple[str, ...] = ()
DEFAULT_IMAGE_MIMETYPES = ("image/jpeg", "image/png", "image/webp")


class HandshakeError(Exception):
    """guacd said something other than what the handshake requires."""


class InstructionChannel(TypingProtocol):
    """Whatever can carry instructions to and from guacd.

    Kept abstract so the handshake is testable against an in-memory fake
    rather than a real socket.
    """

    async def send(self, instruction: Instruction) -> None: ...

    async def read(self) -> Instruction: ...


@dataclass(frozen=True, slots=True)
class Display:
    """Geometry the client is asking guacd to render at."""

    width: int
    height: int
    dpi: int = 96


@dataclass(frozen=True, slots=True)
class Connection:
    """The result of a successful handshake."""

    connection_id: str
    version: tuple[int, int, int]
    #: Parameter names guacd asked for, in the order it asked for them.
    requested: tuple[str, ...] = ()
    #: Parameters we were asked for but had no value to supply.
    unset: tuple[str, ...] = field(default=())


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Read guacd's ``VERSION_1_5_0`` pseudo parameter, if that is what it is."""
    match = _VERSION_PATTERN.match(value)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def format_version(version: tuple[int, int, int]) -> str:
    """Render a version tuple the way guacd expects to read it back."""
    return "VERSION_{}_{}_{}".format(*version)


async def perform_handshake(
    channel: InstructionChannel,
    *,
    protocol: str,
    parameters: dict[str, str],
    display: Display,
    audio: tuple[str, ...] = DEFAULT_AUDIO_MIMETYPES,
    video: tuple[str, ...] = DEFAULT_VIDEO_MIMETYPES,
    image: tuple[str, ...] = DEFAULT_IMAGE_MIMETYPES,
    timezone: str | None = None,
) -> Connection:
    """Take a fresh guacd connection all the way to ``ready``."""
    await channel.send(Instruction.of("select", protocol))

    args = await _expect(channel, "args")
    version, names = _negotiate(args.args)

    await channel.send(Instruction.of("size", display.width, display.height, display.dpi))
    await channel.send(Instruction.of("audio", *audio))
    await channel.send(Instruction.of("video", *video))
    await channel.send(Instruction.of("image", *image))
    if timezone is not None and version >= (1, 1, 0):
        await channel.send(Instruction.of("timezone", timezone))

    values = [parameters.get(name, "") for name in names]
    if version >= (1, 5, 0):
        # Slot 0 is the version, not a parameter: echo what we settled on.
        values.insert(0, format_version(version))

    await channel.send(Instruction("connect", tuple(values)))

    ready = await _expect(channel, "ready")
    if not ready.args:
        raise HandshakeError("guacd sent `ready` without a connection id")

    return Connection(
        connection_id=ready.args[0],
        version=version,
        requested=names,
        unset=tuple(name for name in names if name not in parameters),
    )


def _negotiate(args: tuple[str, ...]) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    """Split guacd's `args` into a protocol version and real parameter names."""
    if not args:
        # Pre-1.5 guacd with a protocol that takes no parameters at all.
        return (1, 0, 0), ()

    server_version = parse_version(args[0])
    if server_version is None:
        # Pre-1.5.0 guacd: no version in the stream, every element is a name.
        return (1, 0, 0), args

    return min(server_version, CLIENT_VERSION), args[1:]


async def _expect(channel: InstructionChannel, opcode: str) -> Instruction:
    """Read the next instruction, insisting on the opcode the handshake needs.

    guacd answers a failed connection with `error` rather than the expected
    opcode, so surface that message instead of a bare mismatch.
    """
    instruction = await channel.read()
    if instruction.opcode == opcode:
        return instruction

    if instruction.opcode == "error":
        message = instruction.args[0] if instruction.args else "no detail"
        raise HandshakeError(f"guacd refused the connection: {message}")

    raise HandshakeError(f"expected `{opcode}` from guacd, got `{instruction.opcode}`")

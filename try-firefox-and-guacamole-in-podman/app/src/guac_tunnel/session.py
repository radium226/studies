"""Reshaping the session, from outside the container it runs in.

This is the part of the study that has no equivalent in the X11 version, and it
exists because of a hole between guacd and wayvnc: neither will change the
desktop size of a live VNC connection. Asking from the client stops at guacd's
`Screen data has not been initialized, yet`; changing it from the server kills
the connection with `Error handling message from VNC server`.

So the resize happens when there is no connection to break. The browser closes
the tunnel, this reshapes sway's output, and the browser reconnects into a
session that is already the shape it asked for. sway and Firefox never restart
-- they are not the connection, and the page survives it.

sway's IPC is a unix socket shared into this container: a six-byte magic, two
native-endian uint32s, and JSON.

Nothing here waits for the previous client to let go first, and that is
deliberate. wayvnc takes a flat ten seconds to admit that a disconnected client
has gone -- long enough to make every rotation feel broken -- and hurrying it
along with `client-disconnect` over its control socket leaves it in a state
where the next resize segfaults, taking sway and Firefox and the open page with
it. Resizing out from under the departing connection is what works: it is on
its way out anyway, and the browser has already opened the next one.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

from loguru import logger

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=6sII")

RUN_COMMAND = 0
GET_OUTPUTS = 3

#: How long to wait for sway to report the size it was asked for. Generous:
#: what is actually being waited on is Firefox reflowing behind it, and the
#: alternative to waiting is connecting to a session mid-resize.
SETTLE_TIMEOUT = 3.0
SETTLE_INTERVAL = 0.05

#: And then a little longer, for wayvnc.
#:
#: sway reporting the new size is not wayvnc having re-captured at it. Connect
#: inside that gap and guacd gets its first framebuffer at the old size and an
#: ExtendedDesktopSize rectangle immediately after -- which it cannot parse:
#: `Error handling message from VNC server`, and the connection is over before
#: anything is drawn. wayvnc exposes no state to poll for this, so: wait, and
#: let the next connection be the thing that observes it.
WAYVNC_SETTLE = 0.5


class SessionError(Exception):
    """sway could not be reached, or refused."""


def encode(message_type: int, payload: str = "") -> bytes:
    body = payload.encode()
    return HEADER.pack(MAGIC, len(body), message_type) + body


async def _exchange(socket_path: str, message_type: int, payload: str = "") -> object:
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except OSError as error:
        raise SessionError(f"cannot reach sway at {socket_path}: {error}") from error

    try:
        writer.write(encode(message_type, payload))
        await writer.drain()

        magic, length, _kind = HEADER.unpack(await reader.readexactly(HEADER.size))
        if magic != MAGIC:
            raise SessionError(f"not a sway socket: {magic!r}")

        return json.loads(await reader.readexactly(length))
    except (asyncio.IncompleteReadError, json.JSONDecodeError) as error:
        raise SessionError(f"malformed reply from sway: {error}") from error
    finally:
        writer.close()


async def current_size(socket_path: str, output: str) -> tuple[int, int] | None:
    """The output's size right now, or None if sway does not have that output."""
    outputs = await _exchange(socket_path, GET_OUTPUTS)
    for entry in outputs:
        if entry.get("name") == output:
            rect = entry["rect"]
            return rect["width"], rect["height"]
    return None


async def reshape(socket_path: str, output: str, width: int, height: int) -> bool:
    """Make the session `width` x `height`, and wait until it is.

    Returns whether it got there. A False is not fatal anywhere: the client
    letterboxes whatever size it is actually given, which is what the VNC
    version of this study always did.
    """
    if not Path(socket_path).exists():
        # The ordinary case for `mise run dev`, where the tunnel runs on the
        # host and there is no session container to reach into. Not an error:
        # everything else still works, at whatever size the session already is.
        logger.debug("no sway socket at {}; leaving the session as it is", socket_path)
        return False

    if await current_size(socket_path, output) == (width, height):
        return True

    logger.info("reshaping {} to {}x{}", output, width, height)
    replies = await _exchange(
        socket_path, RUN_COMMAND, f"output {output} resolution {width}x{height}"
    )
    for reply in replies:
        if not reply.get("success", False):
            raise SessionError(reply.get("error", "sway refused the resize"))

    # sway answers the command before the output has actually changed, and
    # connecting to a session mid-resize is how you get a framebuffer of the
    # old size that never updates.
    deadline = asyncio.get_running_loop().time() + SETTLE_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if await current_size(socket_path, output) == (width, height):
            await asyncio.sleep(WAYVNC_SETTLE)
            return True
        await asyncio.sleep(SETTLE_INTERVAL)

    logger.warning("{} did not reach {}x{} in time", output, width, height)
    return False

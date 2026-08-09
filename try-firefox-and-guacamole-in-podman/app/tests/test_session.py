"""Reshaping the session, against a sway stand-in on a real unix socket.

The wire format is three fields and some JSON, and getting any of them wrong
fails the same way -- a socket that never answers -- so these check the bytes
rather than trusting the struct format string.
"""

from __future__ import annotations

import asyncio
import json
import struct

import pytest

from guac_tunnel.session import (
    GET_OUTPUTS,
    HEADER,
    MAGIC,
    RUN_COMMAND,
    SessionError,
    current_size,
    encode,
    reshape,
)


class FakeSway:
    """A sway that answers IPC on a real socket, and remembers what it was told."""

    def __init__(self, path, width=1080, height=2400, obey=True):
        self.path = str(path)
        self.size = (width, height)
        self.obey = obey
        self.commands: list[str] = []
        self._server = None

    async def start(self):
        self._server = await asyncio.start_unix_server(self._serve, self.path)
        return self

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()

    def _reply(self, kind, payload):
        body = json.dumps(payload).encode()
        return HEADER.pack(MAGIC, len(body), kind) + body

    async def _serve(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(HEADER.size)
                _magic, length, kind = HEADER.unpack(header)
                payload = (await reader.readexactly(length)).decode()

                if kind == RUN_COMMAND:
                    self.commands.append(payload)
                    if self.obey:
                        _, _, size = payload.rpartition(" ")
                        width, height = size.split("x")
                        self.size = (int(width), int(height))
                    writer.write(self._reply(RUN_COMMAND, [{"success": self.obey}]))
                else:
                    writer.write(
                        self._reply(
                            GET_OUTPUTS,
                            [
                                {"name": "OTHER-1", "rect": {"width": 1, "height": 1}},
                                {
                                    "name": "HEADLESS-1",
                                    "rect": {"width": self.size[0], "height": self.size[1]},
                                },
                            ],
                        )
                    )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def sway(tmp_path):
    server = await FakeSway(tmp_path / "sway.sock").start()
    yield server
    await server.stop()


class TestEncoding:
    def test_the_header_is_magic_length_and_type(self):
        frame = encode(RUN_COMMAND, "output HEADLESS-1 resolution 800x600")

        magic, length, kind = HEADER.unpack(frame[: HEADER.size])

        assert magic == b"i3-ipc"
        assert kind == RUN_COMMAND
        assert length == len(b"output HEADLESS-1 resolution 800x600")
        assert frame[HEADER.size :] == b"output HEADLESS-1 resolution 800x600"

    def test_the_lengths_are_native_endian(self):
        """sway reads them as host uint32, not as network order.

        Big-endian here produces a length in the billions, and sway waits for
        a payload that never comes -- so this hangs rather than failing.
        """
        assert HEADER.size == len(MAGIC) + 8
        assert struct.pack("=I", 1) == encode(1, "")[len(MAGIC) + 4 :]


class TestReshape:
    async def test_names_the_output_and_the_size(self, sway, monkeypatch):
        monkeypatch.setattr("guac_tunnel.session.WAYVNC_SETTLE", 0)
        assert await reshape(sway.path, "HEADLESS-1", 2400, 1080) is True

        assert sway.commands == ["output HEADLESS-1 resolution 2400x1080"]

    async def test_says_nothing_when_the_size_already_matches(self, sway):
        """A reconnect that does not change shape should not disturb sway."""
        assert await reshape(sway.path, "HEADLESS-1", 1080, 2400) is True

        assert sway.commands == []

    async def test_reports_the_size_it_did_not_reach(self, sway, monkeypatch):
        """sway answers the command before the output has changed.

        Believing the reply rather than the output is how you connect to a
        session mid-resize and get a framebuffer of the old size.
        """
        monkeypatch.setattr("guac_tunnel.session.SETTLE_TIMEOUT", 0.2)
        monkeypatch.setattr("guac_tunnel.session.WAYVNC_SETTLE", 0)
        sway.obey = False

        with pytest.raises(SessionError):
            await reshape(sway.path, "HEADLESS-1", 2400, 1080)

    async def test_an_absent_socket_is_not_an_error(self, tmp_path):
        """Which is the ordinary case for the tunnel running on the host."""
        assert await reshape(str(tmp_path / "nothing.sock"), "HEADLESS-1", 800, 600) is False

    async def test_reads_the_size_of_the_named_output_only(self, sway):
        assert await current_size(sway.path, "HEADLESS-1") == (1080, 2400)
        assert await current_size(sway.path, "NOT-THERE") is None

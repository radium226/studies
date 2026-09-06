"""End-to-end through Starlette: browser WebSocket <-> guacd socket."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from guac_tunnel.app import create_app
from guac_tunnel.config import Settings
from guac_tunnel.protocol import Instruction, InstructionParser, encode

from .fake_guacd import fake_guacd


def client_for(guacd, **overrides) -> TestClient:
    settings = Settings(guacd_host="127.0.0.1", guacd_port=guacd.port, **overrides)
    return TestClient(create_app(settings))


class Reader:
    """Reads whole instructions off a websocket, whatever the frame sizes."""

    def __init__(self, websocket) -> None:
        self._websocket = websocket
        self._parser = InstructionParser()
        self._pending: list[Instruction] = []

    def take(self, count: int = 1) -> list[Instruction]:
        while len(self._pending) < count:
            self._pending.extend(self._parser.feed(self._websocket.receive_text()))
        taken, self._pending = self._pending[:count], self._pending[count:]
        return taken

    def one(self) -> Instruction:
        return self.take(1)[0]


@contextmanager
def session(client: TestClient, query: str = "") -> Iterator[tuple[object, Reader]]:
    """Open a tunnel and wait for the handshake to actually be finished.

    The UUID frame is only sent after guacd answers `ready`, so consuming it
    is what makes assertions about the handshake race-free -- closing the
    socket any earlier can tear the connection down mid-negotiation.
    """
    with client.websocket_connect(f"/tunnel{query}") as websocket:
        reader = Reader(websocket)
        uuid_frame = reader.one()
        assert uuid_frame.opcode == "", "expected the tunnel UUID frame first"
        yield websocket, reader


class TestTunnelFrame:
    """guacamole-common-js will not open a tunnel it has not been given a UUID for."""

    def test_the_uuid_frame_arrives_before_anything_else(self):
        with (
            fake_guacd(greeting=encode("sync", 0)) as guacd,
            client_for(guacd) as client,
            client.websocket_connect("/tunnel") as websocket,
        ):
            first = InstructionParser().feed(websocket.receive_text())[0]

        assert first.opcode == "", "the first frame must use the internal (empty) opcode"
        assert len(first.args) == 1, "the tunnel UUID must be the only element"

    def test_frames_guacd_sent_with_ready_are_not_lost(self):
        """guacd draws in the same write as `ready`; that must reach the browser."""
        greeting = encode("size", 0, 412, 915) + encode("sync", 1234)

        with (
            fake_guacd(greeting=greeting) as guacd,
            client_for(guacd) as client,
            session(client) as (_, reader),
        ):
            assert [i.opcode for i in reader.take(2)] == ["size", "sync"]


class TestHandshakeThroughTheTunnel:
    def test_selects_the_configured_protocol(self):
        with (
            fake_guacd() as guacd,
            client_for(guacd, remote_protocol="rdp") as client,
            session(client),
        ):
            pass

        assert guacd.last_session.sent("select") == Instruction("select", ("rdp",))

    def test_passes_the_configured_remote_to_guacd(self):
        with (
            fake_guacd(parameters=("hostname", "port")) as guacd,
            client_for(guacd, remote_host="10.0.0.5", remote_port=3390) as client,
            session(client),
        ):
            pass

        assert guacd.last_session.sent("connect").args == ("VERSION_1_5_0", "10.0.0.5", "3390")

    def test_unknown_parameters_are_sent_empty_not_skipped(self):
        with (
            fake_guacd(parameters=("hostname", "wobble", "port")) as guacd,
            client_for(guacd, remote_host="10.0.0.5", remote_port=3390) as client,
            session(client),
        ):
            pass

        assert guacd.last_session.sent("connect").args == (
            "VERSION_1_5_0",
            "10.0.0.5",
            "",
            "3390",
        )

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("?width=800&height=600&dpi=96", ("800", "600", "96")),
            ("", ("412", "915", "96")),
            ("?width=nonsense", ("412", "915", "96")),
            ("?width=999999", ("4096", "915", "96")),
            ("?width=0", ("1", "915", "96")),
        ],
    )
    def test_the_browser_chooses_the_geometry_within_limits(self, query, expected):
        with fake_guacd() as guacd, client_for(guacd) as client, session(client, query):
            pass

        assert guacd.last_session.sent("size").args == expected


class TestPassthrough:
    def test_forwards_client_instructions_to_guacd(self):
        with (
            fake_guacd() as guacd,
            client_for(guacd) as client,
            session(client) as (websocket, _),
        ):
            websocket.send_text(encode("mouse", 120, 340, 1))
            websocket.send_text(encode("key", 65, 1))

        opcodes = guacd.last_session.opcodes
        assert "mouse" in opcodes
        assert "key" in opcodes

    def test_forwards_guacd_instructions_to_the_client(self):
        with (
            fake_guacd() as guacd,
            client_for(guacd) as client,
            session(client) as (_, reader),
        ):
            guacd.push(encode("blob", 0, "AAAA"))
            assert reader.one().opcode == "blob"

    def test_forwards_non_ascii_without_desynchronising(self):
        """A clipboard paste is where character-vs-byte lengths bite."""
        with (
            fake_guacd() as guacd,
            client_for(guacd) as client,
            session(client) as (_, reader),
        ):
            guacd.push(encode("clipboard", "héllo 🦊 日本語") + encode("sync", 1))
            assert reader.take(2) == [
                Instruction("clipboard", ("héllo 🦊 日本語",)),
                Instruction("sync", ("1",)),
            ]


class TestMessageBoundaries:
    """Every WebSocket message must stand alone as complete instructions.

    guacamole-common-js keeps no buffer between messages: one that ends
    mid-instruction fails the whole connection with "Incomplete instruction.".
    So no matter how guacd's stream is chopped up by TCP, what reaches the
    browser has to be re-split on instruction boundaries.
    """

    def test_no_message_ever_ends_mid_instruction(self):
        wire = (
            encode("img", 1, 2, "image/png", "héllo 🦊")
            + encode("blob", 1, "QUFBQQ==")
            + encode("end", 1)
            + encode("sync", 4242)
        )

        with (
            fake_guacd() as guacd,
            client_for(guacd) as client,
            session(client) as (websocket, _),
        ):
            guacd.push(wire, chunk_size=3)  # a pathological but legal split

            messages: list[str] = []
            while "".join(messages) != wire:
                messages.append(websocket.receive_text())

        for message in messages:
            # Parsing each message on its own is what the browser does.
            assert InstructionParser().feed(message), f"not self-contained: {message!r}"
            assert InstructionParser().feed_complete(message) == message


class TestFailures:
    def test_closes_the_socket_when_guacd_is_unreachable(self):
        settings = Settings(guacd_host="127.0.0.1", guacd_port=1)
        with (
            TestClient(create_app(settings)) as client,
            client.websocket_connect("/tunnel") as websocket,
            pytest.raises(WebSocketDisconnect) as disconnect,
        ):
            websocket.receive_text()

        assert disconnect.value.code == 1011
        assert disconnect.value.reason == "guacd unreachable"


class TestPages:
    def test_health_reports_what_it_will_connect_to(self):
        with fake_guacd() as guacd, client_for(guacd, remote_host="10.0.0.5") as client:
            body = client.get("/health").json()

        assert body["remote"] == "vnc://10.0.0.5:5900"

    @pytest.mark.parametrize("path", ["/", "/diagnostics"])
    def test_serves_the_pages(self, path):
        with fake_guacd() as guacd, client_for(guacd) as client:
            assert client.get(path).status_code == 200

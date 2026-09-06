import pytest

from guac_tunnel.handshake import (
    Display,
    HandshakeError,
    format_version,
    parse_version,
    perform_handshake,
)
from guac_tunnel.protocol import Instruction


class FakeGuacd:
    """An in-memory guacd that replays a scripted set of replies."""

    def __init__(self, *replies: Instruction) -> None:
        self._replies = list(replies)
        self.received: list[Instruction] = []

    async def send(self, instruction: Instruction) -> None:
        self.received.append(instruction)

    async def read(self) -> Instruction:
        if not self._replies:
            raise AssertionError("the handshake read more than guacd was scripted to say")
        return self._replies.pop(0)

    def sent(self, opcode: str) -> Instruction:
        for instruction in self.received:
            if instruction.opcode == opcode:
                return instruction
        raise AssertionError(f"the handshake never sent `{opcode}`")

    @property
    def opcodes(self) -> list[str]:
        return [instruction.opcode for instruction in self.received]


def guacd_1_5(*names: str) -> FakeGuacd:
    """A guacd that announces 1.5.0 and asks for the given parameter names."""
    return FakeGuacd(
        Instruction("args", ("VERSION_1_5_0", *names)),
        Instruction("ready", ("$1234-abcd",)),
    )


async def connect(guacd: FakeGuacd, **overrides):
    options = {
        "protocol": "rdp",
        "parameters": {"hostname": "127.0.0.1", "port": "3389"},
        "display": Display(412, 915, 192),
    } | overrides
    return await perform_handshake(guacd, **options)


class TestVersions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("VERSION_1_5_0", (1, 5, 0)),
            ("VERSION_1_1_0", (1, 1, 0)),
            ("VERSION_10_20_30", (10, 20, 30)),
        ],
    )
    def test_parses_version_pseudo_parameters(self, text, expected):
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["hostname", "VERSION_1_5", "VERSION_a_b_c", ""])
    def test_ignores_anything_that_is_not_a_version(self, text):
        assert parse_version(text) is None

    def test_round_trips(self):
        assert parse_version(format_version((1, 5, 0))) == (1, 5, 0)


class TestHandshakeSequence:
    async def test_sends_the_instructions_in_order(self):
        guacd = guacd_1_5("hostname", "port")
        await connect(guacd, timezone="Europe/Paris")

        assert guacd.opcodes == [
            "select",
            "size",
            "audio",
            "video",
            "image",
            "timezone",
            "connect",
        ]

    async def test_selects_the_requested_protocol(self):
        guacd = guacd_1_5("hostname")
        await connect(guacd)
        assert guacd.sent("select") == Instruction("select", ("rdp",))

    async def test_sends_the_display_geometry(self):
        guacd = guacd_1_5("hostname")
        await connect(guacd, display=Display(1024, 768, 96))
        assert guacd.sent("size") == Instruction("size", ("1024", "768", "96"))

    async def test_returns_the_connection_id(self):
        guacd = guacd_1_5("hostname")
        connection = await connect(guacd)
        assert connection.connection_id == "$1234-abcd"
        assert connection.version == (1, 5, 0)


class TestConnectIsPositional:
    async def test_values_line_up_with_the_names_guacd_asked_for(self):
        guacd = guacd_1_5("hostname", "port", "password")
        await connect(guacd, parameters={"port": "3389", "hostname": "127.0.0.1"})

        # Version first, then values in guacd's order -- not ours, and not sorted.
        assert guacd.sent("connect").args == ("VERSION_1_5_0", "127.0.0.1", "3389", "")

    async def test_unsupplied_parameters_become_empty_strings(self):
        guacd = guacd_1_5("hostname", "enable-wallpaper", "client-name")
        connection = await connect(guacd, parameters={"hostname": "127.0.0.1"})

        assert guacd.sent("connect").args == ("VERSION_1_5_0", "127.0.0.1", "", "")
        assert connection.unset == ("enable-wallpaper", "client-name")

    async def test_reports_what_guacd_asked_for(self):
        guacd = guacd_1_5("hostname", "port")
        connection = await connect(guacd)
        assert connection.requested == ("hostname", "port")


class TestVersionNegotiation:
    async def test_echoes_the_negotiated_version_back(self):
        guacd = guacd_1_5("hostname")
        await connect(guacd)
        assert guacd.sent("connect").args[0] == "VERSION_1_5_0"

    async def test_never_negotiates_above_what_the_client_speaks(self):
        guacd = FakeGuacd(
            Instruction("args", ("VERSION_1_9_0", "hostname")),
            Instruction("ready", ("$id",)),
        )
        connection = await connect(guacd)

        assert connection.version == (1, 5, 0)
        assert guacd.sent("connect").args[0] == "VERSION_1_5_0"

    async def test_handles_a_guacd_older_than_the_version_pseudo_parameter(self):
        guacd = FakeGuacd(
            Instruction("args", ("hostname", "port")),
            Instruction("ready", ("$id",)),
        )
        connection = await connect(guacd)

        assert connection.version == (1, 0, 0)
        # No version slot, and no timezone: both post-date 1.0.0.
        assert guacd.sent("connect").args == ("127.0.0.1", "3389")
        assert "timezone" not in guacd.opcodes

    async def test_skips_timezone_before_1_1_0(self):
        guacd = FakeGuacd(
            Instruction("args", ("hostname",)),
            Instruction("ready", ("$id",)),
        )
        await connect(guacd, timezone="Europe/Paris")
        assert "timezone" not in guacd.opcodes

    async def test_handles_a_protocol_taking_no_parameters(self):
        guacd = FakeGuacd(Instruction("args"), Instruction("ready", ("$id",)))
        connection = await connect(guacd)
        assert connection.requested == ()


class TestFailures:
    async def test_surfaces_the_message_when_guacd_refuses(self):
        guacd = FakeGuacd(Instruction("error", ("Unsupported protocol", "519")))
        with pytest.raises(HandshakeError, match="Unsupported protocol"):
            await connect(guacd)

    async def test_rejects_an_unexpected_opcode(self):
        guacd = FakeGuacd(Instruction("nop"))
        with pytest.raises(HandshakeError, match="expected `args`"):
            await connect(guacd)

    async def test_rejects_a_ready_without_a_connection_id(self):
        guacd = FakeGuacd(
            Instruction("args", ("VERSION_1_5_0", "hostname")),
            Instruction("ready"),
        )
        with pytest.raises(HandshakeError, match="without a connection id"):
            await connect(guacd)

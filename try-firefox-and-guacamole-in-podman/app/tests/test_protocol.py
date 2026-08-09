import pytest

from guac_tunnel.protocol import (
    MAX_INSTRUCTION_LENGTH,
    Instruction,
    InstructionParser,
    ProtocolError,
    encode,
)


def parse_all(wire: str) -> list[Instruction]:
    return InstructionParser().feed(wire)


class TestEncoding:
    def test_encodes_opcode_and_args(self):
        assert encode("select", "vnc") == "6.select,3.vnc;"

    def test_encodes_a_bare_opcode(self):
        assert encode("nop") == "3.nop;"

    def test_stringifies_arguments(self):
        assert encode("size", 412, 915, 192) == "4.size,3.412,3.915,3.192;"

    def test_encodes_an_empty_opcode(self):
        # The tunnel's UUID frame: opcode is deliberately empty.
        assert Instruction("", ("abc",)).encode() == "0.,3.abc;"

    def test_encodes_empty_arguments(self):
        assert encode("connect", "", "") == "7.connect,0.,0.;"


class TestCharacterLengths:
    """Lengths count Unicode characters, not UTF-8 bytes."""

    def test_length_counts_characters_not_bytes(self):
        wire = encode("key", "é")
        assert wire == "3.key,1.é;"
        assert len("é".encode()) == 2, "the trap only exists because this is 2 bytes"

    def test_round_trips_astral_plane_characters(self):
        # An emoji is one character here but four bytes on the wire.
        original = Instruction("clipboard", ("🦊",))
        assert parse_all(original.encode()) == [original]

    def test_round_trips_mixed_scripts(self):
        original = Instruction("name", ("Ünïcodé", "日本語", "🦊🔥"))
        assert parse_all(original.encode()) == [original]


class TestParsing:
    def test_parses_a_single_instruction(self):
        assert parse_all("6.select,3.vnc;") == [Instruction("select", ("vnc",))]

    def test_parses_several_instructions_from_one_chunk(self):
        wire = encode("nop") + encode("sync", 1234)
        assert parse_all(wire) == [Instruction("nop"), Instruction("sync", ("1234",))]

    def test_parses_an_empty_opcode(self):
        assert parse_all("0.,3.abc;") == [Instruction("", ("abc",))]

    def test_parses_empty_arguments(self):
        assert parse_all("7.connect,0.,0.;") == [Instruction("connect", ("", ""))]

    def test_returns_nothing_until_an_instruction_completes(self):
        parser = InstructionParser()
        assert parser.feed("6.select,3.vn") == []
        assert parser.feed("c;") == [Instruction("select", ("vnc",))]

    def test_survives_being_fed_one_character_at_a_time(self):
        """TCP read boundaries have nothing to do with instruction boundaries."""
        instructions = [
            Instruction("select", ("vnc",)),
            Instruction("size", ("412", "915", "192")),
            Instruction("clipboard", ("héllo 🦊",)),
        ]
        wire = "".join(i.encode() for i in instructions)

        parser = InstructionParser()
        received = [found for char in wire for found in parser.feed(char)]

        assert received == instructions
        assert parser.pending == ""

    def test_keeps_the_remainder_buffered(self):
        parser = InstructionParser()
        assert parser.feed("3.nop;6.sel") == [Instruction("nop")]
        assert parser.pending == "6.sel"

    def test_does_not_mistake_a_delimiter_inside_a_value(self):
        # Values may contain ',' and ';' -- only the length prefix delimits.
        original = Instruction("clipboard", ("a,b;c.d",))
        assert parse_all(original.encode()) == [original]


class TestForwarding:
    """`feed_complete` exists because the browser's tunnel has no buffer.

    guacamole-common-js parses each WebSocket message on its own and fails the
    connection with "Incomplete instruction." on one that ends mid-instruction,
    so whole instructions are the only safe unit to forward.
    """

    def test_returns_whole_instructions_verbatim(self):
        wire = encode("nop") + encode("sync", 1234)
        assert InstructionParser().feed_complete(wire) == wire

    def test_holds_back_a_partial_tail(self):
        parser = InstructionParser()
        assert parser.feed_complete("3.nop;6.sel") == "3.nop;"
        assert parser.pending == "6.sel"

    def test_releases_the_tail_once_it_completes(self):
        parser = InstructionParser()
        parser.feed_complete("3.nop;6.sel")
        assert parser.feed_complete("ect,3.vnc;") == "6.select,3.vnc;"

    def test_returns_nothing_when_no_instruction_is_complete(self):
        assert InstructionParser().feed_complete("6.sel") == ""

    def test_never_splits_an_instruction_however_the_stream_arrives(self):
        instructions = [
            Instruction("img", ("1", "2", "image/png", "héllo 🦊")),
            Instruction("blob", ("1", "AAAA")),
            Instruction("sync", ("42",)),
        ]
        wire = "".join(i.encode() for i in instructions)

        # Feed in awkward slices, and check every forwarded piece is parseable
        # on its own -- which is exactly what the browser will try to do.
        parser = InstructionParser()
        for size in (1, 3, 7, 13):
            parser = InstructionParser()
            forwarded = [
                parser.feed_complete(wire[at : at + size]) for at in range(0, len(wire), size)
            ]
            for message in forwarded:
                InstructionParser().feed(message)  # must not raise
            assert "".join(forwarded) == wire


class TestMalformedInput:
    def test_rejects_a_non_numeric_length(self):
        with pytest.raises(ProtocolError, match="element length"):
            parse_all("x.select;")

    def test_rejects_an_unexpected_delimiter(self):
        with pytest.raises(ProtocolError, match="expected ',' or ';'"):
            parse_all("3.nop!")

    def test_rejects_an_oversized_incomplete_instruction(self):
        with pytest.raises(ProtocolError, match="exceeds"):
            parse_all(f"{MAX_INSTRUCTION_LENGTH + 10}." + "x" * (MAX_INSTRUCTION_LENGTH + 1))

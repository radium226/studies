"""Tests for the generic `Config` (de)serialization machinery itself.

`test_config.py` covers the pipeline's own config tree; this file covers the
base class against throwaway dataclasses, so every supported field type is
exercised regardless of which types the real configs happen to use today.
"""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Literal

import pytest

from video_analyzer.kernel import Config, ConfigError


@dataclass(frozen=True, slots=True)
class Leaf(Config):
    count: int = 1

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ConfigError(f"count must be >= 0, got {self.count}")


@dataclass(frozen=True, slots=True)
class Everything(Config):
    an_int: int = 1
    a_float: float = 1.5
    a_bool: bool = False
    a_str: str = "hello"
    a_path: Path = Path("some/where.onnx")
    a_choice: Literal["one", "two"] = "one"
    a_pair: tuple[int, int] | None = None
    optional_int: int | None = None
    nested: Leaf = dataclass_field(default_factory=Leaf)
    optional_nested: Leaf | None = None


def test_absent_keys_fall_back_to_field_defaults() -> None:
    assert Everything.from_dict({"an_int": 7}) == Everything(an_int=7)


def test_every_supported_field_type_round_trips() -> None:
    config = Everything(
        an_int=3,
        a_float=2.5,
        a_bool=True,
        a_str="world",
        a_path=Path("/models/x.onnx"),
        a_choice="two",
        a_pair=(640, -1),
        optional_int=9,
        nested=Leaf(count=4),
        optional_nested=Leaf(count=5),
    )
    assert Everything.from_yaml(config.to_yaml()) == config


def test_to_dict_emits_yaml_safe_primitives() -> None:
    data = Everything(a_path=Path("a/b.onnx"), a_pair=(2, 4)).to_dict()
    assert data["a_path"] == "a/b.onnx"
    assert data["a_pair"] == [2, 4]
    assert data["nested"] == {"count": 1}


def test_null_disables_an_optional_nested_section() -> None:
    assert Everything.from_yaml("optional_nested: null\n").optional_nested is None
    assert Everything.from_yaml("optional_nested:\n  count: 2\n").optional_nested == Leaf(
        count=2
    )


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown keys: a_typo"):
        Everything.from_dict({"a_typo": 1})


def test_bool_is_not_accepted_as_a_number() -> None:
    # bool subclasses int, so an unguarded isinstance check would let
    # `an_int: true` through.
    with pytest.raises(ConfigError, match="an_int must be an integer"):
        Everything.from_dict({"an_int": True})
    with pytest.raises(ConfigError, match="a_float must be a number"):
        Everything.from_dict({"a_float": True})


def test_int_is_accepted_where_a_float_is_declared() -> None:
    assert Everything.from_dict({"a_float": 2}).a_float == 2.0


def test_wrong_types_are_rejected() -> None:
    with pytest.raises(ConfigError, match="a_bool must be a boolean"):
        Everything.from_dict({"a_bool": "yes"})
    with pytest.raises(ConfigError, match="a_str must be a string"):
        Everything.from_dict({"a_str": 1})
    with pytest.raises(ConfigError, match="a_path must be a path string"):
        Everything.from_dict({"a_path": 1})
    with pytest.raises(ConfigError, match="nested must be a mapping"):
        Everything.from_dict({"nested": 3})


def test_literal_rejects_a_value_outside_its_choices() -> None:
    with pytest.raises(ConfigError, match="a_choice must be one of one, two"):
        Everything.from_dict({"a_choice": "three"})


def test_fixed_length_tuple_is_length_checked() -> None:
    assert Everything.from_dict({"a_pair": [1, 2]}).a_pair == (1, 2)
    with pytest.raises(ConfigError, match=r"a_pair must have exactly 2 items"):
        Everything.from_dict({"a_pair": [1, 2, 3]})
    with pytest.raises(ConfigError, match=r"a_pair\[1\] must be an integer"):
        Everything.from_dict({"a_pair": [1, "x"]})


def test_validation_error_is_reported_at_its_full_path() -> None:
    with pytest.raises(ConfigError, match=r"^nested\.count must be >= 0"):
        Everything.from_dict({"nested": {"count": -1}})


def test_top_level_validation_error_keeps_the_bare_field_name() -> None:
    with pytest.raises(ConfigError, match=r"^count must be >= 0"):
        Leaf.from_dict({"count": -1})


def test_from_yaml_file_reads_a_document(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a_str: from-file\n")
    assert Everything.from_yaml_file(path).a_str == "from-file"

"""Configuration dataclasses, and the field-driven machinery that (de)serializes them.

Every tunable class in the stack owns a `<ClassName>Config` dataclass holding
exactly its behaviour-affecting parameters. The rule for what belongs here: a
parameter that *describes the data or a collaborator* — a clock, a wrapped
service, a model path, a frame rate probed from the source — stays a
constructor argument; a parameter that *tunes behaviour* and has a sensible
static default lives in the Config.

That split is what lets a Config tree be written to a YAML file: everything in
the document is a real, static choice, and nothing in it is silently
overwritten at runtime by a probed value.

`Config` itself is generic and dependency-free, so `core` and `cli` build their
own config trees on it without reimplementing any parsing.
"""

from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Self, Union, get_args, get_origin, get_type_hints

import yaml
from loguru import logger


class ConfigError(ValueError):
    """Raised for any malformed or out-of-range configuration value.

    Derives from `ValueError` so callers that only care that a value was
    rejected can keep catching that.
    """


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping, got {type(value).__name__}")
    return value


def _reject_unknown_keys(
    mapping: dict[str, Any], known_keys: set[str], context: str
) -> None:
    unknown_keys = set(mapping) - known_keys
    if unknown_keys:
        raise ConfigError(
            f"{context} has unknown keys: {', '.join(sorted(unknown_keys))} "
            f"(known keys: {', '.join(sorted(known_keys))})"
        )


def _qualify(context: str, name: str) -> str:
    return f"{context}.{name}" if context else name


def _parse_scalar(value: Any, annotation: Any, context: str) -> Any:
    # bool first: it is a subclass of int, so an unguarded int check would
    # silently accept `true` wherever a number is expected.
    if annotation is bool:
        if not isinstance(value, bool):
            raise ConfigError(
                f"{context} must be a boolean, got {type(value).__name__}"
            )
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"{context} must be an integer, got {type(value).__name__}"
            )
        return value
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{context} must be a number, got {type(value).__name__}")
        return float(value)
    if annotation is str:
        if not isinstance(value, str):
            raise ConfigError(f"{context} must be a string, got {type(value).__name__}")
        return value
    if annotation is Path:
        if not isinstance(value, str):
            raise ConfigError(f"{context} must be a path string, got {type(value).__name__}")
        return Path(value)
    raise ConfigError(f"{context} has an unsupported field type: {annotation!r}")


def _parse_value(value: Any, annotation: Any, context: str) -> Any:
    """Turn one YAML-decoded value into the type its dataclass field declares.

    The supported set is deliberately closed — an unsupported annotation raises
    rather than passing the raw value through, so a new field type has to be
    taught to this function instead of silently skipping validation.
    """
    origin = get_origin(annotation)

    if origin in (Union, UnionType):
        variants = [arg for arg in get_args(annotation) if arg is not type(None)]
        if value is None:
            return None
        if len(variants) != 1:
            raise ConfigError(
                f"{context} has an unsupported union type: {annotation!r}"
            )
        return _parse_value(value, variants[0], context)

    if origin is Literal:
        choices = get_args(annotation)
        if value not in choices:
            raise ConfigError(
                f"{context} must be one of {', '.join(map(str, choices))}, got {value!r}"
            )
        return value

    if origin is tuple:
        item_annotations = get_args(annotation)
        if not isinstance(value, (list, tuple)):
            raise ConfigError(
                f"{context} must be a list, got {type(value).__name__}"
            )
        if len(value) != len(item_annotations):
            raise ConfigError(
                f"{context} must have exactly {len(item_annotations)} items, "
                f"got {len(value)}"
            )
        return tuple(
            _parse_value(item, item_annotation, f"{context}[{index}]")
            for index, (item, item_annotation) in enumerate(
                zip(value, item_annotations, strict=True)
            )
        )

    if isinstance(annotation, type) and issubclass(annotation, Config):
        return annotation.from_dict(value, context)

    return _parse_scalar(value, annotation, context)


def _to_plain(value: Any) -> Any:
    if isinstance(value, Config):
        return value.to_dict()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


class Config:
    """Mixin giving a frozen dataclass strict dict/YAML (de)serialization.

    Everything is driven off `dataclasses.fields` and the resolved annotations,
    so a new field is picked up automatically — there is no per-class parser to
    forget to update.

    Two properties the whole config story rests on:

    * **Absent keys are omitted from the constructor call**, so the dataclass
      field defaults stay the single source of truth. No default literal is
      ever duplicated into a parser.
    * **Unknown keys are a hard error.** A typo in a config file has to fail
      loudly; silently running with a default the user thought they'd changed
      is the worst outcome available.

    `__slots__ = ()` so `@dataclass(frozen=True, slots=True)` subclasses stay
    slotted.
    """

    __slots__ = ()

    @classmethod
    def from_dict(cls, data: Any, context: str = "") -> Self:
        section = context or cls.__name__
        mapping = _require_mapping(data, section)
        annotations = get_type_hints(cls)
        # `Config` is a mixin for dataclasses, but nothing in the type system
        # says so — every concrete subclass carries the @dataclass decorator.
        config_fields = fields(cls)  # ty: ignore[invalid-argument-type]
        _reject_unknown_keys(mapping, {f.name for f in config_fields}, section)
        kwargs = {
            field.name: _parse_value(
                mapping[field.name],
                annotations[field.name],
                _qualify(context, field.name),
            )
            for field in config_fields
            if field.name in mapping
        }
        try:
            return cls(**kwargs)
        except ConfigError as error:
            # `__post_init__` validators report a bare field name (they have no
            # idea where in a document they were nested); prefix it here, where
            # the path is known, so the message reads `pipeline.batch_gate.
            # max_frames must be >= 1`.
            raise ConfigError(_qualify(context, str(error))) from error

    @classmethod
    def from_yaml(cls, text: str) -> Self:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ConfigError(f"invalid YAML: {error}") from error
        config = cls.from_dict(data)
        logger.debug("{} loaded: {}", cls.__name__, config)
        return config

    @classmethod
    def from_yaml_file(cls, path: Path | str) -> Self:
        path = Path(path)
        logger.info("{}: reading {}", cls.__name__, path)
        return cls.from_yaml(path.read_text())

    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _to_plain(getattr(self, field.name))
            for field in fields(self)  # ty: ignore[invalid-argument-type]
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)


@dataclass(frozen=True, slots=True)
class TokenBucketConfig(Config):
    """Budget a `TokenBucket` may bank up.

    The refill *rate* is not here: it is derived from the stream's frame rate,
    so it is a constructor argument.
    """

    capacity: float = 1.0

    def __post_init__(self) -> None:
        if self.capacity <= 0.0:
            raise ConfigError(f"capacity must be > 0, got {self.capacity}")


@dataclass(frozen=True, slots=True)
class ChannelConfig(Config):
    """How much a `Channel` buffers before its producer blocks."""

    max_size: int = 0  # 0 = unbounded

    def __post_init__(self) -> None:
        if self.max_size < 0:
            raise ConfigError(f"max_size must be >= 0, got {self.max_size}")


@dataclass(frozen=True, slots=True)
class BatchGateConfig(Config):
    """How detection batches are accumulated and fired."""

    max_frames: int = 4
    max_lag_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise ConfigError(f"max_frames must be >= 1, got {self.max_frames}")
        if self.max_lag_ms < 0.0:
            raise ConfigError(f"max_lag_ms must be >= 0, got {self.max_lag_ms}")


@dataclass(frozen=True, slots=True)
class RenderCursorConfig(Config):
    """How far the render cursor trails the newest detection snapshot."""

    lookahead_snapshots: int = 0

    def __post_init__(self) -> None:
        if self.lookahead_snapshots < 0:
            raise ConfigError(
                f"lookahead_snapshots must be >= 0, got {self.lookahead_snapshots}"
            )


@dataclass(frozen=True, slots=True)
class BufferingConfig(Config):
    """How many raw frames the pipeline may hold in flight."""

    # Raw frames are buffered while waiting for their matching detection
    # snapshot to land (detection runs sparsely, at far below video frame
    # rate). Capped so a stalled detector can't grow this buffer without
    # bound; oldest frames are dropped first.
    max_pending_frames: int = 600

    # Every channel that carries whole frames is bounded, so a stage slower
    # than the source applies backpressure all the way back to the decoder
    # instead of quietly accumulating decoded frames. Every queued frame is a
    # full raw image (~2.8 MB at 720x1280 BGR24), so an unbounded queue in
    # front of a stage that can't keep up reaches gigabytes within a minute
    # and then looks like a hang at end of stream, as the pipeline drains a
    # backlog nobody knew was there.
    #
    # Roughly a second of video at 30 fps: enough to absorb scheduling jitter,
    # small enough to keep in-flight frames to a couple of hundred MB. It
    # doesn't need to cover the interpolation lookahead — that buffering
    # happens in `pending_frames`, downstream of this channel.
    #
    # The detection-frame channel gets the same bound: `sample_and_detect`
    # drains it every iteration even while a detection pass is running in the
    # background, so frames never accumulate on the channel itself — the
    # backlog lives in that stage's `pending_frames` buffer, whose
    # `max_pending_frames` drop-oldest cap is the real bound. A stuck detector
    # therefore costs bounded memory and a widening sampling stride, never
    # backpressure on the producer.
    #
    # The snapshot/batch channels stay unbounded: they carry detection
    # metadata, and the frames they reference are already bounded by the
    # pending-frame pruning.
    frame_channel_capacity: int = 30

    def __post_init__(self) -> None:
        if self.max_pending_frames < 1:
            raise ConfigError(
                f"max_pending_frames must be >= 1, got {self.max_pending_frames}"
            )
        if self.frame_channel_capacity < 1:
            raise ConfigError(
                "frame_channel_capacity must be >= 1, got "
                f"{self.frame_channel_capacity}"
            )


@dataclass(frozen=True, slots=True)
class PipelineConfig(Config):
    """Every tuning knob of the pipeline, grouped by the class it configures.

    Services (clock, detectors, sinks) are dependencies, not configuration —
    they stay constructor arguments of `Pipeline`. So is the source's frame
    rate, which is probed at runtime rather than chosen.
    """

    batch_gate: BatchGateConfig = dataclass_field(default_factory=BatchGateConfig)
    render_cursor: RenderCursorConfig = dataclass_field(
        default_factory=RenderCursorConfig
    )
    buffering: BufferingConfig = dataclass_field(default_factory=BufferingConfig)

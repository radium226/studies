from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Self

import yaml
from loguru import logger


class PipelineConfigError(ValueError):
    pass


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PipelineConfigError(
            f"{context} must be a mapping, got {type(value).__name__}"
        )
    return value


def _reject_unknown_keys(
    mapping: dict[str, Any], known_keys: set[str], context: str
) -> None:
    unknown_keys = set(mapping) - known_keys
    if unknown_keys:
        raise PipelineConfigError(
            f"{context} has unknown keys: {', '.join(sorted(unknown_keys))} "
            f"(known keys: {', '.join(sorted(known_keys))})"
        )


def _read_float(mapping: dict[str, Any], key: str, default: float, context: str) -> float:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineConfigError(
            f"{context}.{key} must be a number, got {type(value).__name__}"
        )
    return float(value)


def _read_int(mapping: dict[str, Any], key: str, default: int, context: str) -> int:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineConfigError(
            f"{context}.{key} must be an integer, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True, slots=True)
class BatchingConfig:
    """How detection batches are accumulated and fired."""

    max_frames: int = 4
    max_lag_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise PipelineConfigError(
                f"batching.max_frames must be >= 1, got {self.max_frames}"
            )
        if self.max_lag_ms < 0.0:
            raise PipelineConfigError(
                f"batching.max_lag_ms must be >= 0, got {self.max_lag_ms}"
            )

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        mapping = _require_mapping(data, "batching")
        _reject_unknown_keys(mapping, {"max_frames", "max_lag_ms"}, "batching")
        return cls(
            max_frames=_read_int(mapping, "max_frames", 4, "batching"),
            max_lag_ms=_read_float(mapping, "max_lag_ms", 0.0, "batching"),
        )


@dataclass(frozen=True, slots=True)
class RenderingConfig:
    """How annotated frames are emitted relative to detection snapshots."""

    lookahead_snapshots: int = 0

    def __post_init__(self) -> None:
        if self.lookahead_snapshots < 0:
            raise PipelineConfigError(
                "rendering.lookahead_snapshots must be >= 0, "
                f"got {self.lookahead_snapshots}"
            )

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        mapping = _require_mapping(data, "rendering")
        _reject_unknown_keys(mapping, {"lookahead_snapshots"}, "rendering")
        return cls(
            lookahead_snapshots=_read_int(
                mapping, "lookahead_snapshots", 0, "rendering"
            ),
        )


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Every tuning knob of the pipeline, grouped by concern.

    Services (clock, detectors, sinks) are dependencies, not configuration —
    they stay constructor arguments of `Pipeline`.
    """

    frames_per_second: float = 30.0
    batching: BatchingConfig = field(default_factory=BatchingConfig)
    rendering: RenderingConfig = field(default_factory=RenderingConfig)

    def __post_init__(self) -> None:
        if self.frames_per_second <= 0.0:
            raise PipelineConfigError(
                f"frames_per_second must be > 0, got {self.frames_per_second}"
            )

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        mapping = _require_mapping(data, "pipeline config")
        _reject_unknown_keys(
            mapping,
            {"frames_per_second", "batching", "rendering"},
            "pipeline config",
        )
        config = cls(
            frames_per_second=_read_float(
                mapping, "frames_per_second", 30.0, "pipeline config"
            ),
            batching=BatchingConfig.from_dict(mapping.get("batching")),
            rendering=RenderingConfig.from_dict(mapping.get("rendering")),
        )
        logger.debug("PipelineConfig loaded: {}", config)
        return config

    @classmethod
    def from_yaml(cls, text: str) -> Self:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise PipelineConfigError(f"invalid YAML: {error}") from error
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, path: Path | str) -> Self:
        path = Path(path)
        logger.info("PipelineConfig: reading {}", path)
        return cls.from_yaml(path.read_text())

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_per_second": self.frames_per_second,
            "batching": {
                field_.name: getattr(self.batching, field_.name)
                for field_ in fields(self.batching)
            },
            "rendering": {
                field_.name: getattr(self.rendering, field_.name)
                for field_ in fields(self.rendering)
            },
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

"""One config dataclass per tunable class in `core`, on `kernel`'s `Config` base.

Same rule as `kernel` (see its `config.py`): a parameter that *describes the
data or a collaborator* — a model path, a wrapped service, a stop token, the
source string, a frame rate probed at runtime — stays a constructor argument;
a parameter that *tunes behaviour* and has a sensible static default lives
here.

What deliberately stays a module constant rather than becoming configuration:
the SCRFD/ArcFace geometry (input sizes, strides, normalization mean/std, the
canonical reference landmarks) and the encoder's codec/profile/movflags
settings. Those are model and container-format invariants — changing them
doesn't tune the pipeline, it breaks it. The encoder's keyframe interval is
the clearest case: it is also baked into the literal in
`-force_key_frames expr:gte(t,n_forced*2)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from video_analyzer.kernel import Config, ConfigError

# Lives here rather than in `spline_interpolator.py` so `SplineInterpolatorConfig`
# can name it without a circular import; that module imports it back, and
# `core/__init__.py` re-exports it, so the public name is unchanged.
InterpolationMethod = Literal["cubic", "pchip", "linear"]


@dataclass(frozen=True, slots=True)
class OnnxFaceDetectorConfig(Config):
    """SCRFD detection filtering."""

    score_threshold: float = 0.5
    iou_threshold: float = 0.4

    def __post_init__(self) -> None:
        if not 0.0 <= self.score_threshold <= 1.0:
            raise ConfigError(
                f"score_threshold must be within [0, 1], got {self.score_threshold}"
            )
        if not 0.0 <= self.iou_threshold <= 1.0:
            raise ConfigError(
                f"iou_threshold must be within [0, 1], got {self.iou_threshold}"
            )


@dataclass(frozen=True, slots=True)
class OnnxFaceEmbedderConfig(Config):
    """How many aligned crops ArcFace runs per inference call."""

    max_batch: int = 8

    def __post_init__(self) -> None:
        if self.max_batch < 1:
            raise ConfigError(f"max_batch must be >= 1, got {self.max_batch}")


@dataclass(frozen=True, slots=True)
class ByteTrackTrackerConfig(Config):
    """ByteTrack association and track-lifetime knobs.

    The frame rate is *not* here: it is probed from the source, so it is a
    constructor argument of `ByteTrackTracker`.
    """

    lost_track_buffer: int = 30
    track_activation_threshold: float = 0.25
    minimum_consecutive_frames: int = 1
    iou_buffer_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.lost_track_buffer < 1:
            raise ConfigError(
                f"lost_track_buffer must be >= 1, got {self.lost_track_buffer}"
            )
        if not 0.0 <= self.track_activation_threshold <= 1.0:
            raise ConfigError(
                "track_activation_threshold must be within [0, 1], got "
                f"{self.track_activation_threshold}"
            )
        if self.minimum_consecutive_frames < 1:
            raise ConfigError(
                "minimum_consecutive_frames must be >= 1, got "
                f"{self.minimum_consecutive_frames}"
            )
        if self.iou_buffer_ratio < 0.0:
            raise ConfigError(
                f"iou_buffer_ratio must be >= 0, got {self.iou_buffer_ratio}"
            )


@dataclass(frozen=True, slots=True)
class SplineInterpolatorConfig(Config):
    """Which spline fills the gaps between real detections."""

    method: InterpolationMethod = "pchip"


@dataclass(frozen=True, slots=True)
class HistogramSceneDetectorConfig(Config):
    """Below this per-channel histogram correlation, a frame pair is a cut."""

    correlation_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.correlation_threshold <= 1.0:
            raise ConfigError(
                "correlation_threshold must be within [0, 1], got "
                f"{self.correlation_threshold}"
            )


@dataclass(frozen=True, slots=True)
class FfmpegFrameSourceConfig(Config):
    """How the decoder subprocess reads its source."""

    loop: bool = True
    # ffmpeg-style, so -1 on one axis preserves the aspect ratio.
    resize: tuple[int, int] | None = None
    # Paces how fast ffmpeg emits decoded frames; every frame is still decoded,
    # so this is time compression, not frame dropping. 1.0 is realtime (-re).
    read_rate: float = 1.0
    stop_timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.read_rate <= 0.0:
            raise ConfigError(f"read_rate must be > 0, got {self.read_rate}")
        if self.stop_timeout <= 0.0:
            raise ConfigError(f"stop_timeout must be > 0, got {self.stop_timeout}")


@dataclass(frozen=True, slots=True)
class FfmpegFrameSinkConfig(Config):
    """How the encoder subprocess fragments its output."""

    frag_duration_ms: int = 200
    stop_timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.frag_duration_ms < 1:
            raise ConfigError(
                f"frag_duration_ms must be >= 1, got {self.frag_duration_ms}"
            )
        if self.stop_timeout <= 0.0:
            raise ConfigError(f"stop_timeout must be > 0, got {self.stop_timeout}")


@dataclass(frozen=True, slots=True)
class StopAfterFrameCountConfig(Config):
    """After how many frames read the stop token is set. No default — the
    whole point of wrapping a source in this is to pick a number."""

    max_frames: int

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise ConfigError(f"max_frames must be >= 1, got {self.max_frames}")


@dataclass(frozen=True, slots=True)
class StopOnFirstTrackConfig(Config):
    """What counts as a track worth stopping for, in rendered video frames."""

    min_track_frames: int = 1

    def __post_init__(self) -> None:
        if self.min_track_frames < 1:
            raise ConfigError(
                f"min_track_frames must be >= 1, got {self.min_track_frames}"
            )

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
from dataclasses import field as dataclass_field
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

    # Play the source once. A consumer that needs a stream which never runs dry
    # (`app/`'s live re-encode, say) asks for looping explicitly.
    loop: bool = False
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
    """After how many frames read the stop token is set.

    `enabled` is composition metadata: it tells whoever wires the pipeline
    whether to wrap the source at all, and `StopAfterFrameCount` itself never
    reads it — constructing one is already the decision to use it. It lives
    here so `StopStrategyConfig` can show every strategy's knobs at their
    defaults instead of a bare `null`.
    """

    enabled: bool = False
    # ~10 s at 30 fps. Only meaningful once `enabled`, so the number is a
    # starting point to edit rather than a behaviour anyone inherits silently.
    max_frames: int = 300

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise ConfigError(f"max_frames must be >= 1, got {self.max_frames}")


@dataclass(frozen=True, slots=True)
class StopOnFirstTrackConfig(Config):
    """What counts as a track worth stopping for, in rendered video frames.

    `enabled` works exactly as in `StopAfterFrameCountConfig`.
    """

    enabled: bool = False
    min_track_frames: int = 1

    def __post_init__(self) -> None:
        if self.min_track_frames < 1:
            raise ConfigError(
                f"min_track_frames must be >= 1, got {self.min_track_frames}"
            )


@dataclass(frozen=True, slots=True)
class StopStrategyConfig(Config):
    """Every way a run can end early, each one always present at its defaults.

    Not a choice between alternatives: any combination may be enabled, and the
    first one to set the `kernel.StopToken` ends the run — they all share the
    one token, alongside whatever the composing application adds (`cli`'s
    ffplay window, say). All off, which is the default, means the run lasts
    until the source is exhausted.

    Grouped rather than left as two independent optional sections so the
    document names the axis, and so a dumped config shows what each strategy
    can be told instead of `null`.
    """

    after_frame_count: StopAfterFrameCountConfig = dataclass_field(
        default_factory=StopAfterFrameCountConfig
    )
    on_first_track: StopOnFirstTrackConfig = dataclass_field(
        default_factory=StopOnFirstTrackConfig
    )

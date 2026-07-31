"""The webapp's whole tuning surface, as one YAML-backed document.

`WebappConfig` composes `kernel`'s and `core`'s config trees with this package's own, the same
way `cli/config.py`'s `CliConfig` does — so `--config run.yaml` reaches every knob in the stack
and `--dump-config` prints the exact defaults a bare run uses.

Unlike `CliConfig`, there is no `stop_strategy:` section here: which early-stop strategies are
armed, and their numbers, are chosen live per request from the web form (see `app.py`'s
`/api/source`), not fixed for the whole process. There is also no `track_recording:` section and
no URL-resolution concept — this package only plays local files from a configured directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

from video_analyzer import core, kernel


@dataclass(frozen=True, slots=True)
class ModelsConfig(kernel.Config):
    """Where the ONNX weights live.

    Not bundled with `core` or with this package; these defaults point at the copies already in
    the repo. Relative paths resolve against `webapp/`, since that is `uv run`'s working
    directory.
    """

    scrfd: Path = Path("../app/models/scrfd_10g_kps_dynamic.onnx")
    arcface: Path = Path("../app/models/arcface_w600k_r50_batch.onnx")


@dataclass(frozen=True, slots=True)
class VideoLibraryConfig(kernel.Config):
    """Default directory the browse modal (`/api/browse`) opens on first use.

    Purely a starting point, not an access boundary: once open, the modal can navigate to any
    directory the process can read and pick any file with a recognized video extension. Which
    extensions count as a video is not configurable here: `kernel.Config`'s parser only supports
    fixed-length tuples (`tuple[int, int]`-style), not an open-ended list, and the allowed
    extension set is closer to a format invariant than a per-run tuning choice anyway — see
    `fs_browser.py`'s `VIDEO_EXTENSIONS`.
    """

    directory: Path = Path("../app/assets")


@dataclass(frozen=True, slots=True)
class BroadcasterConfig(kernel.Config):
    """How many recent fMP4 fragments the HTTP broadcaster retains for new/lagging clients."""

    max_fragments: int = 15

    def __post_init__(self) -> None:
        if self.max_fragments < 1:
            raise kernel.ConfigError(
                f"max_fragments must be >= 1, got {self.max_fragments}"
            )


@dataclass(frozen=True, slots=True)
class ServerConfig(kernel.Config):
    """Where uvicorn listens."""

    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise kernel.ConfigError(f"port must be within [1, 65535], got {self.port}")


@dataclass(frozen=True, slots=True)
class WebappConfig(kernel.Config):
    """Everything `video-analyzer-webapp` can be told, minus the per-request file and
    stop-strategy choices (those come from the web form, see `app.py`)."""

    models: ModelsConfig = dataclass_field(default_factory=ModelsConfig)
    pipeline: kernel.PipelineConfig = dataclass_field(default_factory=kernel.PipelineConfig)
    frame_source: core.FfmpegFrameSourceConfig = dataclass_field(
        default_factory=core.FfmpegFrameSourceConfig
    )
    frame_sink: core.FfmpegFrameSinkConfig = dataclass_field(
        default_factory=core.FfmpegFrameSinkConfig
    )
    face_detector: core.OnnxFaceDetectorConfig = dataclass_field(
        default_factory=core.OnnxFaceDetectorConfig
    )
    face_embedder: core.OnnxFaceEmbedderConfig = dataclass_field(
        default_factory=core.OnnxFaceEmbedderConfig
    )
    tracker: core.ByteTrackTrackerConfig = dataclass_field(
        default_factory=core.ByteTrackTrackerConfig
    )
    interpolator: core.SplineInterpolatorConfig = dataclass_field(
        default_factory=core.SplineInterpolatorConfig
    )
    # `null` swaps in the never-cuts NoopSceneDetector, so tracking runs straight through hard
    # cuts — same idiom as CliConfig.
    scene_detector: core.HistogramSceneDetectorConfig | None = dataclass_field(
        default_factory=core.HistogramSceneDetectorConfig
    )
    metrics: core.MetricsCollectorConfig = dataclass_field(
        default_factory=core.MetricsCollectorConfig
    )
    video_library: VideoLibraryConfig = dataclass_field(default_factory=VideoLibraryConfig)
    broadcaster: BroadcasterConfig = dataclass_field(default_factory=BroadcasterConfig)
    server: ServerConfig = dataclass_field(default_factory=ServerConfig)

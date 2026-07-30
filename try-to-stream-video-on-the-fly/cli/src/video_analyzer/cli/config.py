"""The CLI's whole tuning surface, as one YAML-backed document.

`CliConfig` composes `kernel`'s and `core`'s config trees with this package's
own two, so `--config run.yaml` reaches every knob in the stack and
`--dump-config` can print the exact defaults a bare run uses. That replaces
what used to be thirteen click flags; the command line is now just `SOURCE`.

Two conventions worth knowing before editing:

* **An optional section is a feature switch.** `X | None = None` means "this
  stage is off"; giving the section any mapping (even `{}`) turns it on with
  its own defaults. `scene_detector` and `track_recording` work this way.
  The early-stop strategies deliberately do **not**: they live together under
  `stop_strategy`, always present, each armed by its own `enabled:` flag — so
  `--dump-config` shows what every strategy can be told rather than a `null`
  you have to read `core`'s source to decode. Between them these replace six of
  the old flags.
* **This document never overrides a library default.** It can't: a default set
  in a `default_factory` here only applies when the whole section is absent, so
  `frame_source: {read_rate: 4.0}` would silently drop back to the library's
  own field defaults for everything else in that section — including
  `loop`, which would then play the clip forever. A default that cannot
  survive a partial document isn't representable, so where the CLI wanted a
  different value the *library* default was changed instead (`loop=False` in
  `core`, `lookahead_snapshots=3` in `kernel`). Keep it that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

from video_analyzer import core, kernel


@dataclass(frozen=True, slots=True)
class ModelsConfig(kernel.Config):
    """Where the ONNX weights live.

    Not bundled with `core` or with this package; these defaults point at the
    copies already in the repo. Relative paths resolve against `cli/`, since
    that is `uv run`'s working directory.
    """

    scrfd: Path = Path("../app/models/scrfd_10g_kps_dynamic.onnx")
    arcface: Path = Path("../app/models/arcface_w600k_r50_batch.onnx")


@dataclass(frozen=True, slots=True)
class FfplayFrameSinkConfig(kernel.Config):
    """How long to wait for the ffplay window to close on teardown.

    The sink's width/height/fps aren't here: they describe the raw byte stream
    on ffplay's stdin and are derived from the probed source.
    """

    stop_timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.stop_timeout <= 0.0:
            raise kernel.ConfigError(
                f"stop_timeout must be > 0, got {self.stop_timeout}"
            )


@dataclass(frozen=True, slots=True)
class TrackRecordingFrameBroadcasterConfig(kernel.Config):
    """Face-crop buffering for the post-run per-track replay."""

    crop_size: int = 160
    # A track's *first* N rendered frames, ~10 s at 30 fps — about
    # N * crop_size^2 * 3 bytes per track (~22 MB at the defaults). `null`
    # records every frame of every track for the whole run. Note the bound is
    # per track, not global: total memory still grows with the number of
    # distinct track ids a long video accumulates.
    max_crops_per_track: int | None = 300

    def __post_init__(self) -> None:
        if self.crop_size < 16:
            raise kernel.ConfigError(f"crop_size must be >= 16, got {self.crop_size}")
        if self.max_crops_per_track is not None and self.max_crops_per_track < 1:
            raise kernel.ConfigError(
                "max_crops_per_track must be >= 1 or null, got "
                f"{self.max_crops_per_track}"
            )


@dataclass(frozen=True, slots=True)
class CliConfig(kernel.Config):
    """Everything `video-analyzer-cli` can be told, minus the SOURCE itself."""

    models: ModelsConfig = dataclass_field(default_factory=ModelsConfig)
    pipeline: kernel.PipelineConfig = dataclass_field(
        default_factory=kernel.PipelineConfig
    )
    # `read_rate` here is the playback speed factor: it paces the decoder, and
    # the ffplay sink's frame rate is scaled by it to match, so playback stays
    # time-balanced instead of piling up behind a realtime window.
    frame_source: core.FfmpegFrameSourceConfig = dataclass_field(
        default_factory=core.FfmpegFrameSourceConfig
    )
    frame_sink: FfplayFrameSinkConfig = dataclass_field(
        default_factory=FfplayFrameSinkConfig
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
    # `null` swaps in the never-cuts NoopSceneDetector, so tracking runs
    # straight through hard cuts.
    scene_detector: core.HistogramSceneDetectorConfig | None = dataclass_field(
        default_factory=core.HistogramSceneDetectorConfig
    )
    # Always present, every strategy at its defaults, each off until its own
    # `enabled: true`. Not alternatives — any combination may be armed, and the
    # first to set the shared StopToken (including the closed ffplay window,
    # which is this package's own unconfigurable one) ends the run.
    stop_strategy: core.StopStrategyConfig = dataclass_field(
        default_factory=core.StopStrategyConfig
    )
    # Off unless the section is present.
    track_recording: TrackRecordingFrameBroadcasterConfig | None = None

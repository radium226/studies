"""Tests for `CliConfig`, the root document `--config` parses.

The parsing machinery is `kernel`'s and is tested there; what matters here is
that the *composition* holds together — every section reachable, the optional
sections behaving as feature switches, and a partial section never quietly
resetting the rest of itself.
"""

import pytest

from video_analyzer import core, kernel
from video_analyzer.cli.config import CliConfig, TrackRecordingFrameBroadcasterConfig


def test_defaults_round_trip_through_yaml() -> None:
    # This is what --dump-config prints, so it has to parse back exactly.
    assert CliConfig.from_yaml(CliConfig().to_yaml()) == CliConfig()


def test_a_partial_section_does_not_reset_the_rest_of_that_section() -> None:
    """The trap this document has to stay clear of.

    A nested `default_factory` here only applies when the section is *absent*;
    the moment a document mentions `frame_source:` at all, every other key in
    it falls back to the library's own field defaults. So `CliConfig` must
    never carry a default the library doesn't — a clip that suddenly loops
    forever because the user set an unrelated key is exactly the silent
    override the config rewrite exists to prevent.
    """
    config = CliConfig.from_yaml("frame_source:\n  read_rate: 4.0\n")

    assert config.frame_source.read_rate == 4.0
    assert config.frame_source.loop is False
    assert config.frame_source.loop is core.FfmpegFrameSourceConfig().loop

    config = CliConfig.from_yaml("pipeline:\n  batch_gate:\n    max_frames: 16\n")

    assert config.pipeline.batch_gate.max_frames == 16
    assert config.pipeline.render_cursor.lookahead_snapshots == 3
    assert (
        config.pipeline.render_cursor == kernel.PipelineConfig().render_cursor
    )


def test_optional_sections_are_off_by_default() -> None:
    config = CliConfig()
    assert config.track_recording is None
    # ...but scene detection is on, matching the old --scene-detection default.
    assert config.scene_detector == core.HistogramSceneDetectorConfig()


def test_no_stop_strategy_is_armed_by_default() -> None:
    """`stop_strategy` is always present — it's the flags that are off, so
    --dump-config shows each strategy's knobs instead of a bare null."""
    config = CliConfig()

    assert config.stop_strategy == core.StopStrategyConfig()
    assert config.stop_strategy.after_frame_count.enabled is False
    assert config.stop_strategy.on_first_track.enabled is False


def test_a_present_section_switches_its_feature_on() -> None:
    config = CliConfig.from_yaml(
        """
        frame_source:
          read_rate: 4.0
        pipeline:
          batch_gate:
            max_frames: 16
        stop_strategy:
          on_first_track:
            enabled: true
            min_track_frames: 30
        track_recording:
          crop_size: 64
          max_crops_per_track: 60
        """
    )
    assert config.frame_source.read_rate == 4.0
    assert config.pipeline.batch_gate.max_frames == 16
    assert config.stop_strategy.on_first_track == core.StopOnFirstTrackConfig(
        enabled=True, min_track_frames=30
    )
    assert config.track_recording == TrackRecordingFrameBroadcasterConfig(
        crop_size=64, max_crops_per_track=60
    )
    # Untouched sections keep their defaults.
    assert config.pipeline.render_cursor.lookahead_snapshots == 3
    assert config.scene_detector == core.HistogramSceneDetectorConfig()


def test_arming_one_stop_strategy_leaves_the_other_at_its_defaults() -> None:
    """The partial-section trap, one level deeper than the one above: naming a
    strategy must not blank its sibling, and must not blank the rest of its own
    keys either."""
    config = CliConfig.from_yaml(
        "stop_strategy:\n  after_frame_count:\n    enabled: true\n"
    )

    assert config.stop_strategy.after_frame_count.enabled is True
    assert config.stop_strategy.after_frame_count.max_frames == 300
    assert config.stop_strategy.on_first_track == core.StopOnFirstTrackConfig()


def test_an_empty_section_still_switches_its_feature_on() -> None:
    config = CliConfig.from_yaml("track_recording: {}\n")
    assert config.track_recording == TrackRecordingFrameBroadcasterConfig()


def test_null_switches_scene_detection_off() -> None:
    assert CliConfig.from_yaml("scene_detector: null\n").scene_detector is None


def test_model_paths_are_parsed_as_paths() -> None:
    config = CliConfig.from_yaml("models:\n  scrfd: /weights/scrfd.onnx\n")
    assert config.models.scrfd.name == "scrfd.onnx"
    # The other one keeps its default rather than being blanked.
    assert config.models.arcface == CliConfig().models.arcface


def test_unlimited_track_crops_is_spelled_null() -> None:
    config = CliConfig.from_yaml("track_recording:\n  max_crops_per_track: null\n")
    assert config.track_recording is not None
    assert config.track_recording.max_crops_per_track is None


def test_a_typo_anywhere_in_the_tree_is_rejected_with_its_path() -> None:
    with pytest.raises(
        kernel.ConfigError,
        match=r"pipeline\.batch_gate has unknown keys: max_framez",
    ):
        CliConfig.from_yaml("pipeline:\n  batch_gate:\n    max_framez: 4\n")
    with pytest.raises(kernel.ConfigError, match="unknown keys: frame_sync"):
        CliConfig.from_yaml("frame_sync: {}\n")


def test_out_of_range_values_are_rejected_with_their_path() -> None:
    with pytest.raises(
        kernel.ConfigError, match=r"track_recording\.crop_size must be >= 16"
    ):
        CliConfig.from_yaml("track_recording:\n  crop_size: 8\n")
    with pytest.raises(
        kernel.ConfigError, match=r"frame_source\.read_rate must be > 0"
    ):
        CliConfig.from_yaml("frame_source:\n  read_rate: 0\n")

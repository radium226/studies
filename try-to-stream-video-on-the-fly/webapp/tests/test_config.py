"""Tests for `WebappConfig`, the root document `--config` parses.

The parsing machinery is `kernel`'s and is tested there; what matters here is that the
*composition* holds together — every section reachable, the optional `scene_detector` section
behaving as a feature switch, and a partial section never quietly resetting the rest of itself.
Mirrors cli/tests/test_config.py.
"""

import pytest

from video_analyzer import core, kernel
from video_analyzer.webapp.config import WebappConfig


def test_defaults_round_trip_through_yaml() -> None:
    # This is what --dump-config prints, so it has to parse back exactly.
    assert WebappConfig.from_yaml(WebappConfig().to_yaml()) == WebappConfig()


def test_a_partial_section_does_not_reset_the_rest_of_that_section() -> None:
    """The trap this document has to stay clear of.

    A nested `default_factory` here only applies when the section is *absent*; the moment a
    document mentions `frame_source:` at all, every other key in it falls back to the library's
    own field defaults. So `WebappConfig` must never carry a default the library doesn't.
    """
    config = WebappConfig.from_yaml("frame_source:\n  read_rate: 4.0\n")

    assert config.frame_source.read_rate == 4.0
    assert config.frame_source.loop is False
    assert config.frame_source.loop is core.FfmpegFrameSourceConfig().loop

    config = WebappConfig.from_yaml("pipeline:\n  batch_gate:\n    max_frames: 16\n")

    assert config.pipeline.batch_gate.max_frames == 16
    assert config.pipeline.render_cursor.lookahead_snapshots == 3
    assert config.pipeline.render_cursor == kernel.PipelineConfig().render_cursor


def test_a_present_section_switches_its_feature_on() -> None:
    config = WebappConfig.from_yaml(
        """
        frame_source:
          read_rate: 4.0
        pipeline:
          batch_gate:
            max_frames: 16
        """
    )
    assert config.frame_source.read_rate == 4.0
    assert config.pipeline.batch_gate.max_frames == 16
    # Untouched sections keep their defaults.
    assert config.pipeline.render_cursor.lookahead_snapshots == 3
    assert config.scene_detector == core.HistogramSceneDetectorConfig()


def test_scene_detection_is_on_by_default() -> None:
    assert WebappConfig().scene_detector == core.HistogramSceneDetectorConfig()


def test_null_switches_scene_detection_off() -> None:
    assert WebappConfig.from_yaml("scene_detector: null\n").scene_detector is None


def test_model_paths_are_parsed_as_paths() -> None:
    config = WebappConfig.from_yaml("models:\n  scrfd: /weights/scrfd.onnx\n")
    assert config.models.scrfd.name == "scrfd.onnx"
    # The other one keeps its default rather than being blanked.
    assert config.models.arcface == WebappConfig().models.arcface


def test_video_library_directory_is_parsed_as_a_path() -> None:
    config = WebappConfig.from_yaml("video_library:\n  directory: /videos\n")
    assert config.video_library.directory.name == "videos"


def test_server_defaults() -> None:
    config = WebappConfig()
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8000


def test_out_of_range_port_is_rejected() -> None:
    with pytest.raises(kernel.ConfigError, match=r"server\.port must be within"):
        WebappConfig.from_yaml("server:\n  port: 70000\n")


def test_broadcaster_max_fragments_default_and_validation() -> None:
    assert WebappConfig().broadcaster.max_fragments == 15
    with pytest.raises(kernel.ConfigError, match=r"broadcaster\.max_fragments must be >= 1"):
        WebappConfig.from_yaml("broadcaster:\n  max_fragments: 0\n")


def test_a_typo_anywhere_in_the_tree_is_rejected_with_its_path() -> None:
    with pytest.raises(
        kernel.ConfigError,
        match=r"pipeline\.batch_gate has unknown keys: max_framez",
    ):
        WebappConfig.from_yaml("pipeline:\n  batch_gate:\n    max_framez: 4\n")
    with pytest.raises(kernel.ConfigError, match="unknown keys: frame_sync"):
        WebappConfig.from_yaml("frame_sync: {}\n")


def test_out_of_range_values_are_rejected_with_their_path() -> None:
    with pytest.raises(
        kernel.ConfigError, match=r"frame_source\.read_rate must be > 0"
    ):
        WebappConfig.from_yaml("frame_source:\n  read_rate: 0\n")


def test_no_stop_strategy_section_exists() -> None:
    """Unlike CliConfig, stop_strategy is not part of WebappConfig at all — it's chosen live
    per request from the web form (see app.py's /api/source), not fixed for the whole process."""
    with pytest.raises(kernel.ConfigError, match="unknown keys: stop_strategy"):
        WebappConfig.from_yaml("stop_strategy:\n  after_frame_count:\n    enabled: true\n")

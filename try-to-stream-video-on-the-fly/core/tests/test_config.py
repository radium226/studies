"""Validation and YAML round-tripping of `core`'s config dataclasses.

The generic (de)serialization machinery itself is `kernel`'s and is tested
there (`kernel/tests/test_config_base.py`); what's worth pinning here is that
each config actually rejects the values that would break its backend, and that
`core`'s field types (a Literal, an optional int pair) survive a round trip.
"""

import pytest
from video_analyzer.kernel import ConfigError

from video_analyzer import core


def test_defaults_match_the_backends_documented_values() -> None:
    assert core.OnnxFaceDetectorConfig() == core.OnnxFaceDetectorConfig(
        score_threshold=0.5, iou_threshold=0.4
    )
    assert core.OnnxFaceEmbedderConfig().max_batch == 8
    assert core.SplineInterpolatorConfig().method == "pchip"
    assert core.HistogramSceneDetectorConfig().correlation_threshold == 0.5
    assert core.FfmpegFrameSourceConfig().read_rate == 1.0
    assert core.FfmpegFrameSourceConfig().loop is False
    assert core.FfmpegFrameSinkConfig().frag_duration_ms == 200
    assert core.StopOnFirstTrackConfig().min_track_frames == 1


def test_thresholds_outside_zero_to_one_are_rejected() -> None:
    with pytest.raises(ConfigError, match=r"score_threshold must be within \[0, 1\]"):
        core.OnnxFaceDetectorConfig(score_threshold=1.5)
    with pytest.raises(ConfigError, match=r"iou_threshold must be within \[0, 1\]"):
        core.OnnxFaceDetectorConfig(iou_threshold=-0.1)
    with pytest.raises(
        ConfigError, match=r"correlation_threshold must be within \[0, 1\]"
    ):
        core.HistogramSceneDetectorConfig(correlation_threshold=1.5)
    with pytest.raises(
        ConfigError, match=r"track_activation_threshold must be within \[0, 1\]"
    ):
        core.ByteTrackTrackerConfig(track_activation_threshold=2.0)


def test_non_positive_counts_and_rates_are_rejected() -> None:
    with pytest.raises(ConfigError, match="max_batch must be >= 1"):
        core.OnnxFaceEmbedderConfig(max_batch=0)
    with pytest.raises(ConfigError, match="lost_track_buffer must be >= 1"):
        core.ByteTrackTrackerConfig(lost_track_buffer=0)
    with pytest.raises(ConfigError, match="minimum_consecutive_frames must be >= 1"):
        core.ByteTrackTrackerConfig(minimum_consecutive_frames=0)
    with pytest.raises(ConfigError, match="iou_buffer_ratio must be >= 0"):
        core.ByteTrackTrackerConfig(iou_buffer_ratio=-1.0)
    with pytest.raises(ConfigError, match="read_rate must be > 0"):
        core.FfmpegFrameSourceConfig(read_rate=0.0)
    with pytest.raises(ConfigError, match="stop_timeout must be > 0"):
        core.FfmpegFrameSourceConfig(stop_timeout=0.0)
    with pytest.raises(ConfigError, match="frag_duration_ms must be >= 1"):
        core.FfmpegFrameSinkConfig(frag_duration_ms=0)
    with pytest.raises(ConfigError, match="max_frames must be >= 1"):
        core.StopAfterFrameCountConfig(max_frames=0)
    with pytest.raises(ConfigError, match="min_track_frames must be >= 1"):
        core.StopOnFirstTrackConfig(min_track_frames=0)


def test_interpolation_method_is_restricted_to_the_supported_splines() -> None:
    assert core.SplineInterpolatorConfig.from_yaml("method: linear\n").method == "linear"
    with pytest.raises(ConfigError, match="method must be one of cubic, pchip, linear"):
        core.SplineInterpolatorConfig.from_yaml("method: bezier\n")


def test_frame_source_round_trips_through_yaml_including_the_resize_pair() -> None:
    config = core.FfmpegFrameSourceConfig(
        loop=False, resize=(640, -1), read_rate=4.0, stop_timeout=2.5
    )
    assert core.FfmpegFrameSourceConfig.from_yaml(config.to_yaml()) == config


def test_a_typo_in_a_document_is_a_hard_error() -> None:
    with pytest.raises(ConfigError, match="unknown keys: score_threshhold"):
        core.OnnxFaceDetectorConfig.from_yaml("score_threshhold: 0.6\n")

import pytest

from video_analyzer.kernel import (
    BatchGateConfig,
    BufferingConfig,
    ChannelConfig,
    ConfigError,
    PipelineConfig,
    RenderCursorConfig,
    TokenBucketConfig,
)


def test_defaults() -> None:
    config = PipelineConfig()
    assert config.batch_gate == BatchGateConfig(max_frames=4, max_lag_ms=0.0)
    assert config.render_cursor == RenderCursorConfig(lookahead_snapshots=3)
    assert config.buffering == BufferingConfig(
        max_pending_frames=600, frame_channel_capacity=30
    )


def test_from_yaml() -> None:
    config = PipelineConfig.from_yaml(
        """
        batch_gate:
          max_frames: 8
          max_lag_ms: 120.5
        render_cursor:
          lookahead_snapshots: 3
        buffering:
          frame_channel_capacity: 10
        """
    )
    assert config == PipelineConfig(
        batch_gate=BatchGateConfig(max_frames=8, max_lag_ms=120.5),
        render_cursor=RenderCursorConfig(lookahead_snapshots=3),
        buffering=BufferingConfig(frame_channel_capacity=10),
    )


def test_from_yaml_empty_document_gives_defaults() -> None:
    assert PipelineConfig.from_yaml("") == PipelineConfig()


def test_from_yaml_partial_sections_keep_defaults() -> None:
    config = PipelineConfig.from_yaml("batch_gate:\n  max_frames: 2\n")
    assert config.batch_gate.max_frames == 2
    assert config.batch_gate.max_lag_ms == 0.0
    assert config.buffering == BufferingConfig()


def test_yaml_round_trip() -> None:
    config = PipelineConfig(
        batch_gate=BatchGateConfig(max_frames=16, max_lag_ms=50.0),
        render_cursor=RenderCursorConfig(lookahead_snapshots=5),
        buffering=BufferingConfig(max_pending_frames=120, frame_channel_capacity=8),
    )
    assert PipelineConfig.from_yaml(config.to_yaml()) == config


def test_from_yaml_file(tmp_path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text("batch_gate:\n  max_frames: 24\n")
    assert PipelineConfig.from_yaml_file(path).batch_gate.max_frames == 24


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown keys: batch_gat"):
        PipelineConfig.from_yaml("batch_gat: {}\n")


def test_unknown_section_key_rejected() -> None:
    with pytest.raises(ConfigError, match="batch_gate has unknown keys"):
        PipelineConfig.from_yaml("batch_gate:\n  max_frame: 4\n")


def test_wrong_type_rejected() -> None:
    with pytest.raises(ConfigError, match="max_lag_ms must be a number"):
        PipelineConfig.from_yaml("batch_gate:\n  max_lag_ms: fast\n")
    with pytest.raises(ConfigError, match="max_frames must be an integer"):
        PipelineConfig.from_yaml("batch_gate:\n  max_frames: 4.5\n")
    with pytest.raises(ConfigError, match="render_cursor must be a mapping"):
        PipelineConfig.from_yaml("render_cursor: 3\n")


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        PipelineConfig.from_yaml("batch_gate: [unclosed\n")


def test_out_of_range_values_rejected() -> None:
    with pytest.raises(ConfigError, match="max_frames must be >= 1"):
        BatchGateConfig(max_frames=0)
    with pytest.raises(ConfigError, match="max_lag_ms must be >= 0"):
        BatchGateConfig(max_lag_ms=-1.0)
    with pytest.raises(ConfigError, match="lookahead_snapshots must be >= 0"):
        RenderCursorConfig(lookahead_snapshots=-1)
    with pytest.raises(ConfigError, match="capacity must be > 0"):
        TokenBucketConfig(capacity=0.0)
    with pytest.raises(ConfigError, match="max_size must be >= 0"):
        ChannelConfig(max_size=-1)
    with pytest.raises(ConfigError, match="max_pending_frames must be >= 1"):
        BufferingConfig(max_pending_frames=0)
    with pytest.raises(ConfigError, match="frame_channel_capacity must be >= 1"):
        BufferingConfig(frame_channel_capacity=0)


def test_out_of_range_value_in_a_document_reports_its_path() -> None:
    # `__post_init__` only knows the bare field name; `from_dict` is what turns
    # that into a path the user can find in their file.
    with pytest.raises(ConfigError, match=r"batch_gate\.max_frames must be >= 1"):
        PipelineConfig.from_yaml("batch_gate:\n  max_frames: 0\n")

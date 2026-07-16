import pytest

from video_analyzer.kernel import (
    BatchingConfig,
    PipelineConfig,
    PipelineConfigError,
    RenderingConfig,
)


def test_defaults() -> None:
    config = PipelineConfig()
    assert config.frames_per_second == 30.0
    assert config.batching == BatchingConfig(max_frames=4, max_lag_ms=0.0)
    assert config.rendering == RenderingConfig(lookahead_snapshots=0)


def test_from_yaml() -> None:
    config = PipelineConfig.from_yaml(
        """
        frames_per_second: 25
        batching:
          max_frames: 8
          max_lag_ms: 120.5
        rendering:
          lookahead_snapshots: 3
        """
    )
    assert config == PipelineConfig(
        frames_per_second=25.0,
        batching=BatchingConfig(max_frames=8, max_lag_ms=120.5),
        rendering=RenderingConfig(lookahead_snapshots=3),
    )


def test_from_yaml_empty_document_gives_defaults() -> None:
    assert PipelineConfig.from_yaml("") == PipelineConfig()


def test_from_yaml_partial_sections_keep_defaults() -> None:
    config = PipelineConfig.from_yaml("batching:\n  max_frames: 2\n")
    assert config.batching.max_frames == 2
    assert config.batching.max_lag_ms == 0.0
    assert config.frames_per_second == 30.0


def test_yaml_round_trip() -> None:
    config = PipelineConfig(
        frames_per_second=60.0,
        batching=BatchingConfig(max_frames=16, max_lag_ms=50.0),
        rendering=RenderingConfig(lookahead_snapshots=5),
    )
    assert PipelineConfig.from_yaml(config.to_yaml()) == config


def test_from_yaml_file(tmp_path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text("frames_per_second: 24\n")
    assert PipelineConfig.from_yaml_file(path).frames_per_second == 24.0


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(PipelineConfigError, match="unknown keys: frames_per_seond"):
        PipelineConfig.from_yaml("frames_per_seond: 30\n")


def test_unknown_section_key_rejected() -> None:
    with pytest.raises(PipelineConfigError, match="batching has unknown keys"):
        PipelineConfig.from_yaml("batching:\n  max_frame: 4\n")


def test_wrong_type_rejected() -> None:
    with pytest.raises(PipelineConfigError, match="must be a number"):
        PipelineConfig.from_yaml("frames_per_second: fast\n")
    with pytest.raises(PipelineConfigError, match="must be an integer"):
        PipelineConfig.from_yaml("batching:\n  max_frames: 4.5\n")
    with pytest.raises(PipelineConfigError, match="must be a mapping"):
        PipelineConfig.from_yaml("rendering: 3\n")


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(PipelineConfigError, match="invalid YAML"):
        PipelineConfig.from_yaml("batching: [unclosed\n")


def test_out_of_range_values_rejected() -> None:
    with pytest.raises(PipelineConfigError, match="frames_per_second must be > 0"):
        PipelineConfig(frames_per_second=0.0)
    with pytest.raises(PipelineConfigError, match="max_frames must be >= 1"):
        BatchingConfig(max_frames=0)
    with pytest.raises(PipelineConfigError, match="max_lag_ms must be >= 0"):
        BatchingConfig(max_lag_ms=-1.0)
    with pytest.raises(
        PipelineConfigError, match="lookahead_snapshots must be >= 0"
    ):
        RenderingConfig(lookahead_snapshots=-1)

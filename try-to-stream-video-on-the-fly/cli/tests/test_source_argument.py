"""Tests for main()'s command-line surface: the SOURCE argument's file-or-URL
handling, and how it interacts with --dump-config (the one flag that runs
without a SOURCE).

The pipeline itself is out of scope (kernel/core already test every piece it
composes): `_run` is replaced with a recording stub, so these tests only cover
the click-level validation and what gets handed through.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from video_analyzer.cli import main as main_module
from video_analyzer.cli.main import _is_url, main


@pytest.fixture
def recorded_sources(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sources: list[str] = []

    async def _fake_run(source: str, *args: object, **kwargs: object) -> None:
        sources.append(source)

    monkeypatch.setattr(main_module, "_run", _fake_run)
    return sources


def test_is_url_accepts_http_and_https() -> None:
    assert _is_url("http://example.com/v")
    assert _is_url("https://example.com/v")


def test_is_url_rejects_paths_and_other_schemes() -> None:
    assert not _is_url("video.mp4")
    assert not _is_url("/absolute/path/video.mp4")
    assert not _is_url("ftp://example.com/v")
    assert not _is_url("httpsomething/video.mp4")


def test_nonexistent_file_is_rejected(recorded_sources: list[str]) -> None:
    result = CliRunner().invoke(main, ["nonexistent.mp4"])

    assert result.exit_code != 0
    assert "neither an existing video file nor an http(s) URL" in result.output
    assert recorded_sources == []


def test_directory_is_rejected(recorded_sources: list[str], tmp_path: Path) -> None:
    result = CliRunner().invoke(main, [str(tmp_path)])

    assert result.exit_code != 0
    assert recorded_sources == []


def test_existing_file_is_passed_through(
    recorded_sources: list[str], tmp_path: Path
) -> None:
    video = tmp_path / "video.mp4"
    video.touch()

    result = CliRunner().invoke(main, [str(video)])

    assert result.exit_code == 0
    assert recorded_sources == [str(video)]


def test_url_skips_the_existence_check(recorded_sources: list[str]) -> None:
    result = CliRunner().invoke(main, ["https://example.com/watch?v=abc"])

    assert result.exit_code == 0
    assert recorded_sources == ["https://example.com/watch?v=abc"]


def test_missing_source_is_rejected(recorded_sources: list[str]) -> None:
    result = CliRunner().invoke(main, [])

    assert result.exit_code != 0
    assert "Missing argument 'SOURCE'" in result.output
    assert recorded_sources == []


def test_dump_config_needs_no_source_and_runs_nothing(
    recorded_sources: list[str],
) -> None:
    # SOURCE is `required=False` purely so this works; that's why main() has to
    # re-check it by hand.
    result = CliRunner().invoke(main, ["--dump-config"])

    assert result.exit_code == 0
    assert "batch_gate:" in result.output
    assert recorded_sources == []


def test_a_bad_config_file_is_a_usage_error_not_a_traceback(
    recorded_sources: list[str], tmp_path: Path
) -> None:
    video = tmp_path / "video.mp4"
    video.touch()
    config = tmp_path / "run.yaml"
    config.write_text("pipeline:\n  batch_gate:\n    max_framez: 4\n")

    result = CliRunner().invoke(main, [str(video), "--config", str(config)])

    assert result.exit_code != 0
    assert "pipeline.batch_gate has unknown keys: max_framez" in result.output
    assert recorded_sources == []

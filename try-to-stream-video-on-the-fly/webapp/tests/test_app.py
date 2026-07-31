"""Starlette route-level tests: JSON contracts, without spinning up a real pipeline.

`/api/source` requests that would actually reach `PipelineManager._build` (i.e. a valid path and
a valid stop strategy) need a real `ffmpeg`/ONNX weights and are out of scope here — see
`test_pipeline_manager.py` for the fake-backed lifecycle tests and the module docstring's
testing-boundary note in CLAUDE.md. What's covered here is that invalid requests 400 *before*
ever touching the pipeline: path resolution and stop-strategy parsing both fail on pure
validation, with no ffmpeg subprocess involved — pinned by monkeypatching `PipelineManager.start`
to fail the test if it's ever reached.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from video_analyzer.webapp import app as app_module
from video_analyzer.webapp.config import WebappConfig
from video_analyzer.webapp.pipeline_manager import PipelineManager


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "clip.mp4").write_bytes(b"")
    app_module.app.state.config = WebappConfig.from_dict(
        {"video_library": {"directory": str(tmp_path)}}
    )
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _forbid_pipeline_start(monkeypatch: pytest.MonkeyPatch):
    """Every test in this module expects to 400 on pure validation, before `PipelineManager`
    ever gets involved. Failing loudly here catches a validation check silently disappearing."""

    async def _fail(self, source_path, speed_factor, stop_strategy):
        raise AssertionError(f"PipelineManager.start should not have been reached: {source_path}")

    monkeypatch.setattr(PipelineManager, "start", _fail)


def test_index_serves_the_player_page(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_status_is_idle_at_startup(client: TestClient) -> None:
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json() == {"state": "idle", "stream_url": None, "source": None, "error": None}


def test_browse_returns_the_configured_directory_by_default(client: TestClient) -> None:
    res = client.get("/api/browse")
    assert res.status_code == 200
    data = res.json()
    assert [e["name"] for e in data["entries"]] == ["clip.mp4"]
    assert data["entries"][0]["is_dir"] is False


def test_browse_navigates_to_an_explicit_path(client: TestClient, tmp_path: Path) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "other.mp4").write_bytes(b"")
    res = client.get("/api/browse", params={"path": str(subdir)})
    assert res.status_code == 200
    data = res.json()
    assert [e["name"] for e in data["entries"]] == ["other.mp4"]
    assert data["parent"] == str(tmp_path.resolve())


def test_browse_rejects_a_missing_directory(client: TestClient, tmp_path: Path) -> None:
    res = client.get("/api/browse", params={"path": str(tmp_path / "does-not-exist")})
    assert res.status_code == 400


def test_set_source_rejects_an_empty_path(client: TestClient) -> None:
    res = client.post("/api/source", json={"path": ""})
    assert res.status_code == 400


def test_set_source_rejects_a_malformed_stop_strategy(client: TestClient, tmp_path: Path) -> None:
    res = client.post(
        "/api/source",
        json={
            "path": str(tmp_path / "clip.mp4"),
            "stop_strategy": {"after_frame_count": {"max_framez": 1}},
        },
    )
    assert res.status_code == 400
    assert "max_framez" in res.json()["error"]


def test_set_source_rejects_a_relative_path(client: TestClient) -> None:
    res = client.post("/api/source", json={"path": "clip.mp4"})
    assert res.status_code == 400
    assert "absolute" in res.json()["error"]


def test_set_source_rejects_a_non_numeric_speed_factor(client: TestClient, tmp_path: Path) -> None:
    res = client.post(
        "/api/source",
        json={"path": str(tmp_path / "clip.mp4"), "speed_factor": "fast"},
    )
    assert res.status_code == 400
    assert "speed_factor" in res.json()["error"]


def test_set_source_rejects_a_non_positive_speed_factor(client: TestClient, tmp_path: Path) -> None:
    res = client.post(
        "/api/source",
        json={"path": str(tmp_path / "clip.mp4"), "speed_factor": 0},
    )
    assert res.status_code == 400
    assert "speed_factor" in res.json()["error"]


def test_set_source_rejects_an_unresolvable_path(client: TestClient, tmp_path: Path) -> None:
    # No ffmpeg subprocess is ever spawned: fs_browser.resolve_video_path fails on a plain
    # filesystem check before PipelineManager.start gets anywhere near a frame source.
    res = client.post("/api/source", json={"path": str(tmp_path / "missing.mp4")})
    assert res.status_code == 400


def test_stop_when_already_idle_is_a_no_op(client: TestClient) -> None:
    res = client.post("/api/stop")
    assert res.status_code == 200
    assert res.json() == {"state": "idle"}


def test_stream_for_an_unknown_id_returns_410(client: TestClient) -> None:
    res = client.get("/some-stream-id.mp4")
    assert res.status_code == 410

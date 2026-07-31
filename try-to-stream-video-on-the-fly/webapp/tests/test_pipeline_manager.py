"""PipelineManager: generation tracking, and going idle after an unexpected close.

`core.FfmpegFrameSource`/`OnnxFaceDetector`/`OnnxFaceEmbedder`/the frame sink are monkeypatched to
lightweight fakes so this stays a pure-Python unit test (no real ffmpeg subprocess or ONNX model
load) — mirrors app/tests/test_pipeline.py's fake-based style. `PipelineManager` receives an
already-resolved `Path` (path resolution lives in app.py/fs_browser.py now — see
test_app.py's tests for that boundary), so these fake sources don't need a file to actually
exist on disk.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from starlette.applications import Starlette

from video_analyzer import core, kernel
from video_analyzer.webapp import pipeline_manager as pipeline_manager_module
from video_analyzer.webapp.config import WebappConfig
from video_analyzer.webapp.pipeline_manager import PipelineManager


class _FakeFrameSource(kernel.FrameSource):
    @property
    def video_info(self) -> core.VideoInfo:
        return core.VideoInfo(4, 4, 25.0)

    async def read_frame(self) -> None:
        return None  # immediate, clean end of source


@asynccontextmanager
async def _fake_frame_source_cm(
    *_args: object, **_kwargs: object
) -> AsyncIterator[_FakeFrameSource]:
    yield _FakeFrameSource()


class _CrashingFrameSource(kernel.FrameSource):
    @property
    def video_info(self) -> core.VideoInfo:
        return core.VideoInfo(4, 4, 25.0)

    async def read_frame(self) -> None:
        raise RuntimeError("boom")


@asynccontextmanager
async def _crashing_frame_source_cm(
    *_args: object, **_kwargs: object
) -> AsyncIterator[_CrashingFrameSource]:
    yield _CrashingFrameSource()


class _FakeOnnxModel:
    """Stands in for both OnnxFaceDetector and OnnxFaceEmbedder: a sync context manager that
    loads nothing (the real ones lazily load an ONNX session from a model path in __enter__)."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeOnnxModel:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


class _FakeFrameSink:
    def __init__(self, *, blocking: bool = False) -> None:
        self._blocking = blocking

    async def close_stdin(self) -> None:
        pass

    async def read_output_chunk(self, size: int = 65536) -> bytes:
        if self._blocking:
            await asyncio.Event().wait()  # blocks until cancelled at teardown
        # Immediate EOF: the orchestrator's box-parse task closes the broadcaster right away,
        # simulating an unexpected pipeline end (a clean end-of-source) without any explicit
        # stop/switch call.
        return b""

    async def write_frame(self, annotated_frame: object) -> None:
        pass


@asynccontextmanager
async def _fake_frame_sink_cm(*_args: object, **_kwargs: object) -> AsyncIterator[_FakeFrameSink]:
    yield _FakeFrameSink()


@asynccontextmanager
async def _blocking_frame_sink_cm(
    *_args: object, **_kwargs: object
) -> AsyncIterator[_FakeFrameSink]:
    yield _FakeFrameSink(blocking=True)


def _make_manager(monkeypatch, config: WebappConfig | None = None) -> PipelineManager:
    monkeypatch.setattr(core.FfmpegFrameSource, "start", _fake_frame_source_cm)
    monkeypatch.setattr(pipeline_manager_module.core, "OnnxFaceDetector", _FakeOnnxModel)
    monkeypatch.setattr(pipeline_manager_module.core, "OnnxFaceEmbedder", _FakeOnnxModel)
    monkeypatch.setattr(pipeline_manager_module.OverlayFrameSink, "start", _fake_frame_sink_cm)

    app = Starlette()
    return PipelineManager(app, config=config if config is not None else WebappConfig())


async def test_starts_idle(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    assert manager._app.state.broadcaster is None
    assert manager._app.state.stream_id is None
    assert manager.status()["state"] == "idle"


async def test_unexpected_broadcaster_close_goes_idle(monkeypatch, tmp_path: Path) -> None:
    manager = _make_manager(monkeypatch)
    try:
        await manager._build(tmp_path / "test.mp4", 1.0, core.StopStrategyConfig())
        assert manager._app.state.broadcaster is not None
        assert manager._app.state.stream_id is not None
        assert manager._generation == 1

        # The fake sink EOFs immediately, so the watchdog should observe the close and take the
        # pipeline back to idle.
        for _ in range(200):
            if manager._app.state.stream_id is None:
                break
            await asyncio.sleep(0.01)

        assert manager._app.state.broadcaster is None
        assert manager._app.state.stream_id is None
        assert manager.status()["state"] == "idle"
        # Clean end-of-source (no crash) -> no failure popup recorded.
        assert manager.status()["error"] is None
    finally:
        await manager.aclose()


async def test_pipeline_crash_goes_idle_with_error(monkeypatch, tmp_path: Path) -> None:
    manager = _make_manager(monkeypatch)
    monkeypatch.setattr(core.FfmpegFrameSource, "start", _crashing_frame_source_cm)
    monkeypatch.setattr(pipeline_manager_module.OverlayFrameSink, "start", _blocking_frame_sink_cm)
    source_path = tmp_path / "boom-source.mp4"
    try:
        await manager._build(source_path, 1.0, core.StopStrategyConfig())

        for _ in range(200):
            if manager._app.state.stream_id is None:
                break
            await asyncio.sleep(0.01)

        status = manager.status()
        assert status["state"] == "idle"
        # A crashed task records a failure popup with the source label + reason.
        error = status["error"]
        assert error is not None
        assert error["source"] == str(source_path)
        assert "boom" in error["message"]
        assert error["id"] == 1
    finally:
        await manager.aclose()


async def test_speed_factor_overrides_read_rate_and_scales_encoder_fps(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _capturing_frame_source_cm(source, *, config=None):
        captured["frame_source_config"] = config
        yield _FakeFrameSource()

    @asynccontextmanager
    async def _capturing_frame_sink_cm(width, height, fps, *, config=None):
        captured["sink_fps"] = fps
        yield _FakeFrameSink()

    manager = _make_manager(monkeypatch)
    monkeypatch.setattr(core.FfmpegFrameSource, "start", _capturing_frame_source_cm)
    monkeypatch.setattr(pipeline_manager_module.OverlayFrameSink, "start", _capturing_frame_sink_cm)
    try:
        await manager._build(tmp_path / "test.mp4", 2.0, core.StopStrategyConfig())
        # read_rate and loop are the per-request frame_source knobs; resize and stop_timeout
        # still come from the static config. No loop was passed here, so the YAML's value stands.
        assert captured["frame_source_config"].read_rate == 2.0
        assert captured["frame_source_config"].loop == WebappConfig().frame_source.loop
        # _FakeFrameSource reports a native fps of 25.0.
        assert captured["sink_fps"] == 25.0 * 2.0
    finally:
        await manager.aclose()


@asynccontextmanager
async def _capturing_frame_source_cm(source, *, config=None):
    _CAPTURED_FRAME_SOURCE_CONFIGS.append(config)
    yield _FakeFrameSource()


_CAPTURED_FRAME_SOURCE_CONFIGS: list[object] = []


@pytest.mark.parametrize(
    ("configured_loop", "requested_loop", "expected_loop"),
    [
        # An explicit request wins either way round...
        (False, True, True),
        (True, False, False),
        # ...and None means "no preference", so the YAML's own value stands. That distinction is
        # what keeps a configured loop: true from being silently cleared by an API client that
        # simply doesn't send the field.
        (True, None, True),
        (False, None, False),
    ],
)
async def test_loop_is_a_per_request_override_of_the_configured_default(
    monkeypatch,
    tmp_path: Path,
    configured_loop: bool,
    requested_loop: bool | None,
    expected_loop: bool,
) -> None:
    _CAPTURED_FRAME_SOURCE_CONFIGS.clear()
    config = WebappConfig.from_dict({"frame_source": {"loop": configured_loop}})
    manager = _make_manager(monkeypatch, config)
    monkeypatch.setattr(core.FfmpegFrameSource, "start", _capturing_frame_source_cm)
    try:
        await manager._build(
            tmp_path / "test.mp4", 1.0, core.StopStrategyConfig(), loop=requested_loop
        )
        assert _CAPTURED_FRAME_SOURCE_CONFIGS[-1].loop is expected_loop
    finally:
        await manager.aclose()


async def test_aclose_stops_the_watchdog_from_firing_further(monkeypatch, tmp_path: Path) -> None:
    manager = _make_manager(monkeypatch)
    await manager._build(tmp_path / "test.mp4", 1.0, core.StopStrategyConfig())

    await manager.aclose()
    generation_at_close = manager._generation

    # Give any straggler watchdog task a chance to misfire post-shutdown.
    await asyncio.sleep(0.1)
    assert manager._generation == generation_at_close

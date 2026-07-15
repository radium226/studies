"""PipelineManager: generation tracking, and going idle after an unexpected close.

Writer/Engine are monkeypatched to lightweight fakes so this stays a pure-Python
unit test (no real ffmpeg subprocess or ONNX model load).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette

from video_streamer import pipeline as pipeline_module
from video_streamer.pipeline import PipelineManager


class _FakeLoader:
    @property
    def video_info(self) -> tuple[int, int, float]:
        return 4, 4, 25.0

    async def frames(self) -> AsyncIterator[None]:
        return
        yield  # pragma: no cover - makes this an async generator that yields nothing


@asynccontextmanager
async def _fake_loader_cm() -> AsyncIterator[_FakeLoader]:
    yield _FakeLoader()


class _CrashingLoader:
    @property
    def video_info(self) -> tuple[int, int, float]:
        return 4, 4, 25.0

    async def frames(self) -> AsyncIterator[None]:
        raise RuntimeError("boom")
        yield  # pragma: no cover - unreachable; makes this an async generator


@asynccontextmanager
async def _crashing_loader_cm() -> AsyncIterator[_CrashingLoader]:
    yield _CrashingLoader()


class _BlockingWriter:
    """Never EOFs on its own, so the *only* thing that can close the
    broadcaster is a crashed pipeline task (the failure path)."""

    async def close_stdin(self) -> None:
        pass

    async def read_output_chunk(self) -> bytes:
        await asyncio.Event().wait()  # blocks until cancelled at teardown
        return b""


@asynccontextmanager
async def _blocking_writer_cm(*_args: object, **_kwargs: object) -> AsyncIterator[_BlockingWriter]:
    yield _BlockingWriter()


class _FakeWriter:
    async def close_stdin(self) -> None:
        pass

    async def read_output_chunk(self) -> bytes:
        # Immediate EOF: the orchestrator's box-parse task closes the
        # broadcaster right away, simulating an unexpected pipeline end
        # (a clean end-of-source) without any explicit stop/switch call.
        return b""


@asynccontextmanager
async def _fake_writer_cm(*_args: object, **_kwargs: object) -> AsyncIterator[_FakeWriter]:
    yield _FakeWriter()


class _FakeEngine:
    pass


@asynccontextmanager
async def _fake_engine_cm(*_args: object, **_kwargs: object) -> AsyncIterator[_FakeEngine]:
    yield _FakeEngine()


def _make_manager(monkeypatch) -> PipelineManager:
    monkeypatch.setattr(pipeline_module.Writer, "start", _fake_writer_cm)
    monkeypatch.setattr(pipeline_module.Engine, "start", _fake_engine_cm)

    app = Starlette()
    app.state.resize = None
    app.state.speed_factor = 1.0
    app.state.frag_duration_ms = 500
    app.state.scrfd_batch_frames = 4
    app.state.arcface_batch_crops = 8
    return PipelineManager(app, sample_video=Path("unused.mp4"), models_dir=Path("unused"))


async def test_starts_idle(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    assert manager._app.state.broadcaster is None
    assert manager._app.state.engine is None
    assert manager._app.state.stream_id is None
    assert manager.status()["state"] == "idle"


async def test_unexpected_broadcaster_close_goes_idle(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    try:
        await manager._build(_fake_loader_cm, "test")
        assert manager._app.state.broadcaster is not None
        assert manager._app.state.stream_id is not None
        assert manager._generation == 1

        # The fake writer EOFs immediately, so the watchdog should observe the
        # close and take the pipeline back to idle.
        for _ in range(200):
            if manager._app.state.stream_id is None:
                break
            await asyncio.sleep(0.01)

        assert manager._app.state.broadcaster is None
        assert manager._app.state.engine is None
        assert manager._app.state.stream_id is None
        assert manager.status()["state"] == "idle"
        # Clean end-of-source (no crash) -> no failure popup recorded.
        assert manager.status()["error"] is None
    finally:
        await manager.aclose()


async def test_pipeline_crash_goes_idle_with_error(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    monkeypatch.setattr(pipeline_module.Writer, "start", _blocking_writer_cm)
    try:
        await manager._build(_crashing_loader_cm, "boom-source")

        for _ in range(200):
            if manager._app.state.stream_id is None:
                break
            await asyncio.sleep(0.01)

        status = manager.status()
        assert status["state"] == "idle"
        # A crashed task records a failure popup with the source label + reason.
        error = status["error"]
        assert error is not None
        assert error["source"] == "boom-source"
        assert "boom" in error["message"]
        assert error["id"] == 1
    finally:
        await manager.aclose()


async def test_aclose_stops_the_watchdog_from_firing_further(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    await manager._build(_fake_loader_cm, "test")

    await manager.aclose()
    generation_at_close = manager._generation

    # Give any straggler watchdog task a chance to misfire post-shutdown.
    await asyncio.sleep(0.1)
    assert manager._generation == generation_at_close

"""PipelineManager: generation tracking, and auto-heal after an unexpected close.

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


class _FakeWriter:
    async def close_stdin(self) -> None:
        pass

    async def read_output_chunk(self) -> bytes:
        # Immediate EOF: the orchestrator's box-parse task closes the
        # broadcaster right away, simulating an unexpected pipeline end
        # without any explicit switch_to_url call.
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
    monkeypatch.setattr(pipeline_module, "_INITIAL_HEAL_BACKOFF_S", 0.01)
    monkeypatch.setattr(pipeline_module, "_MAX_HEAL_BACKOFF_S", 0.02)

    app = Starlette()
    app.state.repeat_input_video = False
    app.state.resize = None
    app.state.speed_factor = 1.0
    app.state.frag_duration_ms = 500
    app.state.scrfd_batch_frames = 4
    app.state.arcface_batch_crops = 8
    return PipelineManager(app, sample_video=Path("unused.mp4"), models_dir=Path("unused"))


async def test_unexpected_broadcaster_close_triggers_auto_rebuild(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    try:
        await manager._build(_fake_loader_cm)
        first_broadcaster = manager._app.state.broadcaster
        first_stream_id = manager._app.state.stream_id
        assert manager._generation == 1

        for _ in range(200):
            if manager._generation > 1:
                break
            await asyncio.sleep(0.01)

        assert manager._generation > 1
        assert manager._app.state.broadcaster is not first_broadcaster
        assert manager._app.state.stream_id != first_stream_id
    finally:
        await manager.aclose()


async def test_aclose_stops_the_watchdog_from_rebuilding_further(monkeypatch) -> None:
    manager = _make_manager(monkeypatch)
    await manager._build(_fake_loader_cm)

    await manager.aclose()
    generation_at_close = manager._generation

    # Give any straggler watchdog task a chance to misfire post-shutdown.
    await asyncio.sleep(0.1)
    assert manager._generation == generation_at_close

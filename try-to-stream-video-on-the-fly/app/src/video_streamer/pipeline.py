"""Owns the shared pipeline's lifecycle: initial build at startup, and live
rebuilds when the input source changes (e.g. a URL submitted through the UI).

Wraps loader -> writer -> engine -> broadcaster -> orchestrator in an
AsyncExitStack so the whole stack can be torn down and rebuilt on demand,
not just via a fixed `async with` block scope.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path

from starlette.applications import Starlette

from video_streamer.broadcaster import Broadcaster
from video_streamer.engine import Engine
from video_streamer.input_video import (
    InputVideoLoader,
    SyntheticInputVideoLoader,
    UrlInputVideoLoader,
)
from video_streamer.orchestrator import Orchestrator
from video_streamer.writer import Writer


class PipelineManager:
    def __init__(self, app: Starlette, *, sample_video: Path, models_dir: Path) -> None:
        self._app = app
        self._sample_video = sample_video
        self._models_dir = models_dir
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()

        # Snapshot the CLI-configured knobs once; only the input source
        # itself ever changes via switch_to_url().
        self._loop: bool = app.state.repeat_input_video
        self._resize: tuple[int, int] | None = app.state.resize
        self._speed_factor: float = app.state.speed_factor
        self._frag_duration_ms: int = app.state.frag_duration_ms
        self._scrfd_batch_frames: int = app.state.scrfd_batch_frames
        self._arcface_batch_crops: int = app.state.arcface_batch_crops

    async def build_initial(self) -> None:
        if self._app.state.input_video == "synthetic":
            loader_cm = SyntheticInputVideoLoader.start(
                self._sample_video,
                loop=self._loop,
                resize=self._resize,
                speed_factor=self._speed_factor,
            )
        else:
            loader_cm = UrlInputVideoLoader.start(
                self._app.state.input_video_url,
                loop=self._loop,
                resize=self._resize,
                speed_factor=self._speed_factor,
            )
        async with self._lock:
            await self._build(loader_cm)

    async def switch_to_url(self, url: str) -> tuple[int, int, float]:
        async with self._lock:
            return await self._build(
                UrlInputVideoLoader.start(
                    url,
                    loop=self._loop,
                    resize=self._resize,
                    speed_factor=self._speed_factor,
                )
            )

    async def _build(
        self, loader_cm: AbstractAsyncContextManager[InputVideoLoader]
    ) -> tuple[int, int, float]:
        # Teardown-first: only one ffmpeg pair + ONNX engine ever runs at a
        # time, at the cost of a client-visible gap during the rebuild. The
        # closed broadcaster makes /stream.mp4 return 410, and the player
        # polls until the new pipeline is up. If the build fails there is no
        # pipeline at all; players keep polling until the next successful
        # switch.
        await self._stack.aclose()
        self._stack = AsyncExitStack()

        new_stack = AsyncExitStack()
        try:
            loader = await new_stack.enter_async_context(loader_cm)
            w, h, fps = loader.video_info
            writer = await new_stack.enter_async_context(
                Writer.start(
                    w,
                    h,
                    fps * self._speed_factor,
                    frag_duration_ms=self._frag_duration_ms,
                )
            )
            engine = await new_stack.enter_async_context(
                Engine.start(
                    model_dir=self._models_dir,
                    fps=fps,
                    scrfd_batch_frames=self._scrfd_batch_frames,
                    arcface_batch_crops=self._arcface_batch_crops,
                )
            )
            broadcaster = await new_stack.enter_async_context(Broadcaster.start())
            await new_stack.enter_async_context(
                Orchestrator.start(loader, engine, writer, broadcaster)
            )
        except BaseException:
            await new_stack.aclose()
            raise

        self._stack = new_stack
        self._app.state.broadcaster = broadcaster
        self._app.state.engine = engine
        return w, h, fps

    async def aclose(self) -> None:
        await self._stack.aclose()

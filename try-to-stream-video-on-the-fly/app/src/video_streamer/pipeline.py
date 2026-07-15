"""Owns the shared pipeline's lifecycle: initial build at startup, and live
rebuilds when the input source changes (e.g. a URL submitted through the UI).

Wraps loader -> writer -> engine -> broadcaster -> orchestrator in an
AsyncExitStack so the whole stack can be torn down and rebuilt on demand,
not just via a fixed `async with` block scope.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
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

logger = logging.getLogger(__name__)

# Backoff for auto-rebuilding after the pipeline closes unexpectedly (source
# exhaustion the loop flag didn't cover, a decoder/encoder crash, a dropped
# network connection, ...). Doubles on repeated failures, capped, and resets
# once a rebuild succeeds.
_INITIAL_HEAL_BACKOFF_S = 2.0
_MAX_HEAL_BACKOFF_S = 30.0

LoaderFactory = Callable[[], AbstractAsyncContextManager[InputVideoLoader]]


class PipelineManager:
    def __init__(self, app: Starlette, *, sample_video: Path, models_dir: Path) -> None:
        self._app = app
        self._sample_video = sample_video
        self._models_dir = models_dir
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._generation = 0
        self._closing = False
        self._watchdog_tasks: set[asyncio.Task[None]] = set()

        # Snapshot the CLI-configured knobs once; only the input source
        # itself ever changes via switch_to_url().
        self._loop: bool = app.state.repeat_input_video
        self._resize: tuple[int, int] | None = app.state.resize
        self._speed_factor: float = app.state.speed_factor
        self._frag_duration_ms: int = app.state.frag_duration_ms
        self._scrfd_batch_frames: int = app.state.scrfd_batch_frames
        self._arcface_batch_crops: int = app.state.arcface_batch_crops

    def _synthetic_loader_factory(self) -> LoaderFactory:
        return lambda: SyntheticInputVideoLoader.start(
            self._sample_video,
            loop=self._loop,
            resize=self._resize,
            speed_factor=self._speed_factor,
        )

    def _url_loader_factory(self, url: str) -> LoaderFactory:
        return lambda: UrlInputVideoLoader.start(
            url,
            loop=self._loop,
            resize=self._resize,
            speed_factor=self._speed_factor,
        )

    async def build_initial(self) -> None:
        if self._app.state.input_video == "synthetic":
            loader_factory = self._synthetic_loader_factory()
        else:
            loader_factory = self._url_loader_factory(self._app.state.input_video_url)
        async with self._lock:
            await self._build(loader_factory)

    async def switch_to_url(self, url: str) -> tuple[int, int, float]:
        async with self._lock:
            return await self._build(self._url_loader_factory(url))

    async def _build(self, loader_factory: LoaderFactory) -> tuple[int, int, float]:
        # Teardown-first: only one ffmpeg pair + ONNX engine ever runs at a
        # time, at the cost of a client-visible gap during the rebuild. The
        # closed broadcaster makes the live stream endpoint return 410, and
        # the player polls until the new pipeline is up. A watchdog spawned below
        # auto-retries this same source if the new pipeline later closes on
        # its own (source exhaustion, a crash, ...). If this build itself
        # raises, self._generation is never bumped, so the watchdog watching
        # the *old* (just-closed-by-aclose-above) broadcaster still matches
        # the current generation once it wakes - it takes over and retries
        # the previous source instead of leaving the pipeline dead.
        await self._stack.aclose()
        self._stack = AsyncExitStack()

        new_stack = AsyncExitStack()
        try:
            loader = await new_stack.enter_async_context(loader_factory())
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
        self._app.state.stream_id = str(uuid.uuid4())
        self._generation += 1
        generation = self._generation
        task = asyncio.create_task(
            self._watch_and_heal(broadcaster, generation, loader_factory),
            name=f"pipeline-watchdog-{generation}",
        )
        self._watchdog_tasks.add(task)
        task.add_done_callback(self._watchdog_tasks.discard)
        return w, h, fps

    async def _watch_and_heal(
        self,
        broadcaster: Broadcaster,
        generation: int,
        loader_factory: LoaderFactory,
        backoff_s: float | None = None,
    ) -> None:
        if backoff_s is None:
            backoff_s = _INITIAL_HEAL_BACKOFF_S
        await broadcaster.wait_closed()
        await asyncio.sleep(backoff_s)
        async with self._lock:
            # A newer generation already replaced this pipeline (an explicit
            # /api/source switch, or an earlier retry) - nothing to heal.
            if self._closing or generation != self._generation:
                return
            logger.warning(
                "pipeline (generation %d) closed unexpectedly; auto-rebuilding "
                "the same source after %.0fs backoff",
                generation,
                backoff_s,
            )
            try:
                await self._build(loader_factory)
            except Exception:
                logger.exception(
                    "auto-rebuild of generation %d failed; will retry", generation
                )
                next_backoff = min(backoff_s * 2, _MAX_HEAL_BACKOFF_S)
                task = asyncio.create_task(
                    self._watch_and_heal(broadcaster, generation, loader_factory, next_backoff),
                    name=f"pipeline-watchdog-{generation}-retry",
                )
                self._watchdog_tasks.add(task)
                task.add_done_callback(self._watchdog_tasks.discard)

    async def aclose(self) -> None:
        self._closing = True
        tasks = list(self._watchdog_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stack.aclose()

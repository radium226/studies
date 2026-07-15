"""Owns the shared pipeline's lifecycle: it starts idle (no source), builds a
pipeline when the UI requests one, and tears back down to idle when the source
ends, fails, or is explicitly stopped.

Wraps loader -> writer -> engine -> broadcaster -> orchestrator in an
AsyncExitStack so the whole stack can be torn down and rebuilt on demand,
not just via a fixed `async with` block scope.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path

from loguru import logger
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

LoaderFactory = Callable[[], AbstractAsyncContextManager[InputVideoLoader]]

SYNTHETIC_SOURCE_LABEL = "test pattern"


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

        # The currently-running pipeline's orchestrator (for its crash reason)
        # and a human label for the active source; both None while idle.
        self._orchestrator: Orchestrator | None = None
        self._current_source_label: str | None = None
        # Last unexpected-failure record, surfaced to the browser as a popup.
        # Monotonic id lets the player show each failure exactly once.
        self._last_error: dict[str, object] | None = None
        self._error_seq = 0

        # Snapshot the CLI-configured global tuning knobs once; the input
        # source (and its per-source loop flag) arrive via start_url/start_synthetic.
        self._resize: tuple[int, int] | None = app.state.resize
        self._speed_factor: float = app.state.speed_factor
        self._frag_duration_ms: int = app.state.frag_duration_ms
        self._scrfd_batch_frames: int = app.state.scrfd_batch_frames
        self._arcface_batch_crops: int = app.state.arcface_batch_crops

        # Start idle: no broadcaster/engine/stream yet.
        self._set_idle_state()

    def _set_idle_state(self) -> None:
        self._app.state.broadcaster = None
        self._app.state.engine = None
        self._app.state.stream_id = None

    def _synthetic_loader_factory(self, loop: bool) -> LoaderFactory:
        return lambda: SyntheticInputVideoLoader.start(
            self._sample_video,
            loop=loop,
            resize=self._resize,
            speed_factor=self._speed_factor,
        )

    def _url_loader_factory(self, url: str, loop: bool) -> LoaderFactory:
        return lambda: UrlInputVideoLoader.start(
            url,
            loop=loop,
            resize=self._resize,
            speed_factor=self._speed_factor,
        )

    async def start_url(self, url: str, loop: bool) -> tuple[int, int, float]:
        async with self._lock:
            return await self._build(self._url_loader_factory(url, loop), url)

    async def start_synthetic(self, loop: bool) -> tuple[int, int, float]:
        async with self._lock:
            return await self._build(
                self._synthetic_loader_factory(loop), SYNTHETIC_SOURCE_LABEL
            )

    async def go_idle(self) -> None:
        """Explicit stop: tear the pipeline down to idle. Not a failure, so no
        error popup is recorded."""
        async with self._lock:
            await self._go_idle_locked(error=None)

    async def _build(
        self, loader_factory: LoaderFactory, source_label: str
    ) -> tuple[int, int, float]:
        # Teardown-first: only one ffmpeg pair + ONNX engine ever runs at a
        # time, at the cost of a client-visible gap during the rebuild. The
        # closed broadcaster makes the live stream endpoint return 410, and
        # the player polls until the new pipeline is up. A watchdog spawned
        # below takes the pipeline back to idle if it later closes on its own
        # (source exhaustion, a crash, ...). If this build itself raises,
        # self._generation is never bumped, so the watchdog watching the *old*
        # (just-closed-by-aclose-above) broadcaster still matches the current
        # generation once it wakes - it takes the pipeline to idle instead of
        # leaving a half-dead stack.
        await self._stack.aclose()
        self._stack = AsyncExitStack()
        self._set_idle_state()

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
            orchestrator = await new_stack.enter_async_context(
                Orchestrator.start(loader, engine, writer, broadcaster)
            )
        except BaseException:
            await new_stack.aclose()
            raise

        self._stack = new_stack
        self._orchestrator = orchestrator
        self._current_source_label = source_label
        self._last_error = None
        self._app.state.broadcaster = broadcaster
        self._app.state.engine = engine
        self._app.state.stream_id = str(uuid.uuid4())
        self._generation += 1
        generation = self._generation
        task = asyncio.create_task(
            self._watch_and_idle(broadcaster, generation),
            name=f"pipeline-watchdog-{generation}",
        )
        self._watchdog_tasks.add(task)
        task.add_done_callback(self._watchdog_tasks.discard)
        return w, h, fps

    async def _watch_and_idle(self, broadcaster: Broadcaster, generation: int) -> None:
        await broadcaster.wait_closed()
        async with self._lock:
            # A newer generation already replaced this pipeline (an explicit
            # stop, or a source switch) - nothing to do; that action owns the
            # transition.
            if self._closing or generation != self._generation:
                return
            # The pipeline closed on its own. A crash carries a failure reason
            # (-> popup); a natural end-of-source does not (-> silent idle).
            reason = self._orchestrator.failure_reason if self._orchestrator else None
            source = self._current_source_label
            error: dict[str, object] | None = (
                {"source": source, "message": reason} if reason is not None else None
            )
            if error is not None:
                logger.warning(
                    "pipeline (generation {}) failed on source {!r}: {}",
                    generation,
                    source,
                    reason,
                )
            else:
                logger.info(
                    "pipeline (generation {}) reached end of source {!r}; going idle",
                    generation,
                    source,
                )
            await self._go_idle_locked(error=error)

    async def _go_idle_locked(self, error: dict[str, object] | None) -> None:
        # Bump the generation first so any other watchdog waiting on the lock
        # sees a mismatch and returns instead of double-handling this close.
        self._generation += 1
        await self._stack.aclose()
        self._stack = AsyncExitStack()
        self._orchestrator = None
        self._current_source_label = None
        self._set_idle_state()
        if error is not None:
            self._error_seq += 1
            self._last_error = {"id": self._error_seq, **error}

    def status(self) -> dict[str, object]:
        stream_id = self._app.state.stream_id
        return {
            "state": "playing" if stream_id is not None else "idle",
            "stream_url": f"/{stream_id}.mp4" if stream_id is not None else None,
            "source": self._current_source_label,
            "error": self._last_error,
        }

    async def aclose(self) -> None:
        self._closing = True
        tasks = list(self._watchdog_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stack.aclose()

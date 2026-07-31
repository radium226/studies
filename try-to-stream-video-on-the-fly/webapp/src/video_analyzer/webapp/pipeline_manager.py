"""Owns the shared pipeline's lifecycle: it starts idle (no source), builds a pipeline when the
UI requests one (a resolved file path plus a stop strategy), and tears back down to idle when the
source ends, fails, or is explicitly stopped.

Wraps frame source -> face detector/embedder -> frame sink -> broadcaster -> orchestrator in an
AsyncExitStack so the whole stack can be torn down and rebuilt on demand, not just via a fixed
`async with` block scope. Adapted from app/pipeline.py's PipelineManager, composing
kernel.Pipeline + core's real backends instead of app/'s own Engine/InputVideoLoader.

Resolving *what* `source` should be (a bare filename against a configured directory, an
absolute path from the browse modal, a page URL resolved via yt-dlp to a direct media URL, ...) is
the caller's job (see app.py's `/api/source`) — this class only ever receives an
already-validated, ffmpeg-ready source string.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace
from typing import Literal, TypedDict

from loguru import logger
from starlette.applications import Starlette

from video_analyzer import core, kernel

from .broadcaster import Broadcaster
from .config import WebappConfig
from .noop_scene_detector import NoopSceneDetector
from .orchestrator import Orchestrator
from .overlay_frame_sink import OverlayFrameSink
from .system_clock import SystemClock
from .track_video_manager import TrackVideoManager


class SourceError(TypedDict):
    """Unexpected-failure record surfaced to the browser as a popup. The monotonic id lets the
    player show each failure exactly once."""

    id: int
    source: str | None
    message: str


class PipelineStatus(TypedDict):
    """JSON payload of /api/status, polled by the player."""

    state: Literal["idle", "playing"]
    stream_url: str | None
    source: str | None
    error: SourceError | None


class PipelineManager:
    def __init__(self, app: Starlette, *, config: WebappConfig) -> None:
        self._app = app
        self._config = config
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._generation = 0
        self._closing = False
        self._watchdog_tasks: set[asyncio.Task[None]] = set()

        # The currently-running pipeline's orchestrator (for its crash reason) and a human label
        # for the active source; both None while idle.
        self._orchestrator: Orchestrator | None = None
        self._current_source_label: str | None = None
        self._last_error: SourceError | None = None
        self._error_seq = 0

        # Start idle: no broadcaster/stream yet.
        self._set_idle_state()

    def _set_idle_state(self) -> None:
        self._app.state.broadcaster = None
        self._app.state.stream_id = None
        self._app.state.track_manager = None
        # Metrics belong to one build: a new pipeline gets a fresh collector rather than carrying
        # the previous source's timings into its first window. /metrics reports {} while None.
        self._app.state.metrics = None

    async def start(
        self,
        source: str,
        speed_factor: float,
        stop_strategy: core.StopStrategyConfig,
        *,
        label: str | None = None,
    ) -> core.VideoInfo:
        async with self._lock:
            return await self._build(source, speed_factor, stop_strategy, label=label)

    async def go_idle(self) -> None:
        """Explicit stop: tear the pipeline down to idle. Not a failure, so no error popup is
        recorded."""
        async with self._lock:
            await self._go_idle_locked(error=None)

    async def _build(
        self,
        source: str,
        speed_factor: float,
        stop_strategy: core.StopStrategyConfig,
        *,
        label: str | None = None,
    ) -> core.VideoInfo:
        # Teardown-first: only one ffmpeg pair + ONNX pipeline ever runs at a time, at
        # the cost of a client-visible gap during the rebuild. The closed broadcaster makes the
        # live stream endpoint return 410, and the player polls until the new pipeline is up. A
        # watchdog spawned below takes the pipeline back to idle if it later closes on its own
        # (source exhaustion, an armed stop strategy, a crash, ...). If this build itself raises,
        # self._generation is never bumped, so the watchdog watching the *old*
        # (just-closed-by-teardown-above) broadcaster still matches the current generation once
        # it wakes - it takes the pipeline to idle instead of leaving a half-dead stack.
        await self._teardown_locked()

        new_stack = AsyncExitStack()
        try:
            stop_token = kernel.StopToken()

            # read_rate (the playback speed factor) is the one frame_source knob chosen live per
            # request rather than fixed in the YAML — everything else in the section (loop,
            # resize, stop_timeout) still comes from the static config.
            frame_source_config = replace(self._config.frame_source, read_rate=speed_factor)
            raw_frame_source = await new_stack.enter_async_context(
                core.FfmpegFrameSource.start(source, config=frame_source_config)
            )
            # Always the file's native fps — read_rate paces how fast frames come out, it
            # doesn't change what the video *is*.
            video_info = raw_frame_source.video_info
            width, height, fps = video_info.width, video_info.height, video_info.fps

            frame_source: kernel.FrameSource = raw_frame_source
            if stop_strategy.after_frame_count.enabled:
                frame_source = core.StopAfterFrameCount(
                    frame_source, stop_token, config=stop_strategy.after_frame_count
                )

            # One clock for the whole build, shared by the pipeline's BatchGate and the metrics
            # windows so both measure elapsed time on the same monotonic scale.
            clock = SystemClock()
            metrics = core.MetricsCollector(clock, config=self._config.metrics)

            # The Metered* wrappers are how /metrics sees inside kernel.Pipeline at all: the
            # timings it reports happen in stages the composing app can't otherwise reach (see
            # core/CLAUDE.md). They only time the await, so the real work is untouched.
            face_detector: kernel.FaceDetector = core.MeteredFaceDetector(
                new_stack.enter_context(
                    core.OnnxFaceDetector(
                        self._config.models.scrfd, config=self._config.face_detector
                    )
                ),
                metrics,
                clock,
            )
            face_embedder: kernel.FaceEmbedder = core.MeteredFaceEmbedder(
                new_stack.enter_context(
                    core.OnnxFaceEmbedder(
                        self._config.models.arcface, config=self._config.face_embedder
                    )
                ),
                metrics,
                clock,
            )

            # -r on the encoder is the other half of the time compression: the decoder hands us
            # frames N x faster (speed_factor), and the encoder emits them N x faster too, so the
            # browser sees a smooth, live-balanced N x fast-forward.
            frame_sink = await new_stack.enter_async_context(
                OverlayFrameSink.start(
                    width,
                    height,
                    fps * speed_factor,
                    config=self._config.frame_sink,
                )
            )

            broadcaster = await new_stack.enter_async_context(
                Broadcaster.start(max_fragments=self._config.broadcaster.max_fragments)
            )

            # Same time-compression reasoning as the main encoder above: track streams are fed at
            # whatever rate frames actually arrive at broadcast_frame, which speed_factor scales
            # too (via the decoder's -readrate), so their own encoder fps must match it exactly
            # to play back at the same perceived speed as the main stream.
            track_video_manager = await new_stack.enter_async_context(
                TrackVideoManager.start(
                    fps * speed_factor,
                    sink_config=self._config.frame_sink,
                    max_fragments=self._config.broadcaster.max_fragments,
                )
            )

            # Metered innermost, so processed_fps counts every rendered frame regardless of
            # which stop strategies happen to be armed on top of it.
            frame_broadcaster: kernel.FrameBroadcaster = core.MeteredFrameBroadcaster(
                track_video_manager, metrics
            )
            if stop_strategy.on_first_track.enabled:
                frame_broadcaster = core.StopOnFirstTrack(
                    frame_broadcaster, stop_token, config=stop_strategy.on_first_track
                )

            # Native fps here too: the tracker is stepped once per detection snapshot, not per
            # video frame, so its wall-clock update rate doesn't move with playback speed.
            tracker: kernel.Tracker = core.ByteTrackTracker(fps, config=self._config.tracker)

            pipeline = kernel.Pipeline(
                clock=clock,
                scene_detector=(
                    core.HistogramSceneDetector(config=self._config.scene_detector)
                    if self._config.scene_detector is not None
                    else NoopSceneDetector()
                ),
                face_detector=face_detector,
                face_embedder=face_embedder,
                tracker=tracker,
                interpolator=core.SplineInterpolator(config=self._config.interpolator),
                frame_sink=frame_sink,
                frame_broadcaster=frame_broadcaster,
                # Deliberately *not* scaled by read_rate: this is the detection budget
                # (BatchGate's token bucket refill rate, in tokens per wall-clock second), and
                # holding it at native fps is what makes a faster playback cost detection
                # coverage rather than CPU.
                frames_per_second=fps,
                config=self._config.pipeline,
            )

            orchestrator = await new_stack.enter_async_context(
                Orchestrator.start(pipeline, frame_source, frame_sink, broadcaster, stop_token)
            )
        except BaseException:
            await new_stack.aclose()
            raise

        self._stack = new_stack
        self._orchestrator = orchestrator
        self._current_source_label = label if label is not None else str(source)
        self._last_error = None
        self._app.state.broadcaster = broadcaster
        self._app.state.stream_id = str(uuid.uuid4())
        self._app.state.track_manager = track_video_manager
        self._app.state.metrics = metrics
        self._generation += 1
        generation = self._generation
        task = asyncio.create_task(
            self._watch_and_idle(broadcaster, generation),
            name=f"pipeline-watchdog-{generation}",
        )
        self._watchdog_tasks.add(task)
        task.add_done_callback(self._watchdog_tasks.discard)
        return video_info

    async def _watch_and_idle(self, broadcaster: Broadcaster, generation: int) -> None:
        await broadcaster.wait_closed()
        async with self._lock:
            # A newer generation already replaced this pipeline (an explicit stop, or a source
            # switch) - nothing to do; that action owns the transition.
            if self._closing or generation != self._generation:
                return
            # The pipeline closed on its own. A crash carries a failure reason (-> popup); a
            # natural end-of-source or an armed stop strategy firing does not (-> silent idle).
            reason = self._orchestrator.failure_reason if self._orchestrator else None
            source = self._current_source_label
            error: SourceError | None = None
            if reason is not None:
                self._error_seq += 1
                error = SourceError(id=self._error_seq, source=source, message=reason)
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

    async def _go_idle_locked(self, error: SourceError | None) -> None:
        # Bump the generation first so any other watchdog waiting on the lock sees a mismatch and
        # returns instead of double-handling this close.
        self._generation += 1
        await self._teardown_locked()
        if error is not None:
            self._last_error = error

    async def _teardown_locked(self) -> None:
        """Close the current stack (if any) and reset all per-pipeline state.

        Callers must hold self._lock. Leaves self._last_error alone: an explicit stop or a fresh
        build decides what happens to it.
        """
        await self._stack.aclose()
        self._stack = AsyncExitStack()
        self._orchestrator = None
        self._current_source_label = None
        self._set_idle_state()

    def status(self) -> PipelineStatus:
        stream_id = self._app.state.stream_id
        return PipelineStatus(
            state="playing" if stream_id is not None else "idle",
            stream_url=f"/{stream_id}.mp4" if stream_id is not None else None,
            source=self._current_source_label,
            error=self._last_error,
        )

    async def aclose(self) -> None:
        self._closing = True
        tasks = list(self._watchdog_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stack.aclose()

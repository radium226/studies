import asyncio
import contextlib
from asyncio import TaskGroup
from typing import NamedTuple

from loguru import logger

from .batch_gate import BatchGate
from .channel import Channel
from .config import PipelineConfig
from .stop_token import StopToken
from .models import AnnotatedFrame, Face, Frame, FrameIndex, Snapshot, TrackedFace
from .services import (
    Clock,
    FaceDetector,
    FaceEmbedder,
    FrameBroadcaster,
    FrameSink,
    FrameSource,
    Interpolator,
    SceneDetector,
    Tracker,
)

# Raw frames are buffered while waiting for their matching detection snapshot to
# land (detection runs sparsely, at far below video frame rate). Capped so a
# stalled detector can't grow this buffer without bound; oldest frames are
# dropped first, matching the equivalent cap in the pre-kernel implementation.
_MAX_PENDING_FRAMES = 600

# The two channels that carry whole frames are bounded, so a sink slower than
# the source applies backpressure all the way back to the decoder instead of
# quietly accumulating decoded frames. Every queued frame is a full raw image
# (~2.8 MB at 720x1280 BGR24), so an unbounded queue in front of a sink that
# can't keep up reaches gigabytes within a minute and then looks like a hang at
# end of stream, as the pipeline drains a backlog nobody knew was there.
#
# Roughly a second of video at 30 fps: enough to absorb scheduling jitter,
# small enough to keep in-flight frames to a couple of hundred MB. It doesn't
# need to cover the interpolation lookahead — that buffering happens in
# `pending_frames`, downstream of this channel.
#
# The snapshot/batch channels stay unbounded: they carry detection metadata, and
# the frames they reference are already bounded by the pending-frame pruning.
_FRAME_CHANNEL_CAPACITY = 30


def _sample_evenly(indices: list[FrameIndex], max_count: int) -> list[FrameIndex]:
    """Pick up to `max_count` indices, evenly spread across `indices`.

    Plain integer/float arithmetic on purpose — sampling is a scheduling
    decision, not numeric work, so it stays free of a numpy dependency.
    """
    if len(indices) <= max_count:
        return indices
    if max_count <= 1:
        return [indices[-1]]
    step = (len(indices) - 1) / (max_count - 1)
    return [indices[round(i * step)] for i in range(max_count)]


class _AdvanceResult[FaceEmbeddingT](NamedTuple):
    frame_index: FrameIndex
    is_exact: bool
    bracket: tuple[Snapshot[TrackedFace[FaceEmbeddingT]], Snapshot[TrackedFace[FaceEmbeddingT]]]
    faces: list[TrackedFace[FaceEmbeddingT]]


class _RenderCursor[FaceEmbeddingT]:
    """Lags `lookahead_snapshots` snapshots behind the newest tracked snapshot so
    every frame it emits lies strictly between two real detections — never
    extrapolated. One `advance()` call is expected per incoming video frame,
    matching however many `push_snapshot()` calls have landed by then
    (detection and video run at unrelated rates).

    This holds only the *timing* bookkeeping (which segment, which frame index
    to render, when to prune old snapshots) — the actual coordinate math is
    delegated to the injected `Interpolator`, which is pure numeric fill.
    """

    def __init__(
        self,
        lookahead_snapshots: int,
        interpolator: Interpolator[TrackedFace[FaceEmbeddingT]],
    ) -> None:
        self._lookahead_snapshots = lookahead_snapshots
        self._interpolator = interpolator
        self._snapshots: list[Snapshot[TrackedFace[FaceEmbeddingT]]] = []
        self._segment_index = 0
        self._render_cursor: float | None = None

    def push_snapshot(self, snapshot: Snapshot[TrackedFace[FaceEmbeddingT]]) -> None:
        self._snapshots.append(snapshot)

    @property
    def can_advance(self) -> bool:
        # Need the segment end (segment_index+1) plus `lookahead_snapshots` more
        # snapshots ahead of it.
        return len(self._snapshots) > self._segment_index + self._lookahead_snapshots + 1

    async def advance(self) -> _AdvanceResult[FaceEmbeddingT] | None:
        if not self.can_advance:
            return None
        frame_index, is_exact = self._advance_segment()
        bracket = (
            self._snapshots[self._segment_index],
            self._snapshots[self._segment_index + 1],
        )
        faces = await self._interpolate(frame_index)
        return _AdvanceResult(
            frame_index=frame_index, is_exact=is_exact, bracket=bracket, faces=faces
        )

    def _advance_segment(self) -> tuple[FrameIndex, bool]:
        segment_start = self._snapshots[self._segment_index]
        segment_end = self._snapshots[self._segment_index + 1]

        if self._render_cursor is None:
            self._render_cursor = float(segment_start.frame_index)

        # Clamp to the current segment for pure interpolation.
        frame_index = int(
            min(max(self._render_cursor, segment_start.frame_index), segment_end.frame_index)
        )
        is_exact = frame_index in (segment_start.frame_index, segment_end.frame_index)
        self._render_cursor += 1.0

        # Advance to the next segment once the cursor leaves the current one,
        # but only while enough lookahead remains beyond the new segment end.
        while (
            self._render_cursor > self._snapshots[self._segment_index + 1].frame_index
            and len(self._snapshots) > self._segment_index + self._lookahead_snapshots + 2
        ):
            self._segment_index += 1

        # Snapshots behind the spline window can never be used again.
        drop = self._segment_index - self._lookahead_snapshots
        if drop > 0:
            del self._snapshots[:drop]
            self._segment_index -= drop

        # Cap the cursor at the newest frame we can still interpolate, so a gap
        # in detection arrivals doesn't let the cursor free-run and then lurch
        # forward once the next burst of snapshots lands.
        newest = len(self._snapshots) - self._lookahead_snapshots - 1
        if newest >= 1:
            cap = float(self._snapshots[newest].frame_index)
            if self._render_cursor > cap:
                self._render_cursor = cap

        return frame_index, is_exact

    async def _interpolate(
        self, frame_index: FrameIndex
    ) -> list[TrackedFace[FaceEmbeddingT]]:
        """Interpolate every face of the current segment's start snapshot,
        matching control points across snapshots by track id."""
        segment_start = self._snapshots[self._segment_index]

        window_start = max(0, self._segment_index - self._lookahead_snapshots)
        window_end = min(
            len(self._snapshots), self._segment_index + self._lookahead_snapshots + 2
        )
        window = self._snapshots[window_start:window_end]
        span_start = window[0].frame_index
        span = window[-1].frame_index - span_start + 1

        results: list[TrackedFace[FaceEmbeddingT]] = []
        for tracked_face in segment_start.faces:
            track_id = tracked_face.track_id
            slots: list[TrackedFace[FaceEmbeddingT] | None] = [None] * span
            known = 0
            for snapshot in window:
                match = next(
                    (
                        candidate_face
                        for candidate_face in snapshot.faces
                        if candidate_face.track_id == track_id
                    ),
                    None,
                )
                if match is not None:
                    slots[snapshot.frame_index - span_start] = match
                    known += 1
            if known < 2:
                results.append(tracked_face)
                continue
            filled = await self._interpolator.interpolate(slots)
            results.append(filled[frame_index - span_start])
        return results


class Pipeline[FrameContentT, FaceEmbeddingT]:

    clock: Clock
    scene_detector: SceneDetector[FrameContentT]
    face_detector: FaceDetector[FrameContentT]
    face_embedder: FaceEmbedder[FrameContentT, FaceEmbeddingT]
    tracker: Tracker[FaceEmbeddingT]
    interpolator: Interpolator[TrackedFace[FaceEmbeddingT]]
    frame_sink: FrameSink[FrameContentT, TrackedFace[FaceEmbeddingT]]
    frame_broadcaster: FrameBroadcaster[FrameContentT, TrackedFace[FaceEmbeddingT]]
    config: PipelineConfig

    def __init__(
        self,
        clock: Clock,
        scene_detector: SceneDetector[FrameContentT],
        face_detector: FaceDetector[FrameContentT],
        face_embedder: FaceEmbedder[FrameContentT, FaceEmbeddingT],
        tracker: Tracker[FaceEmbeddingT],
        interpolator: Interpolator[TrackedFace[FaceEmbeddingT]],
        frame_sink: FrameSink[FrameContentT, TrackedFace[FaceEmbeddingT]],
        frame_broadcaster: FrameBroadcaster[FrameContentT, TrackedFace[FaceEmbeddingT]],
        *,
        config: PipelineConfig | None = None,
    ) -> None:
        self.clock = clock
        self.scene_detector = scene_detector
        self.face_detector = face_detector
        self.face_embedder = face_embedder
        self.tracker = tracker
        self.interpolator = interpolator
        self.frame_sink = frame_sink
        self.frame_broadcaster = frame_broadcaster
        self.config = config if config is not None else PipelineConfig()
        logger.debug("Pipeline configured: {}", self.config)

    async def produce_frames(
        self,
        frame_source: FrameSource[FrameContentT],
        detection_frame_channel: Channel[Frame[FrameContentT]],
        render_frame_channel: Channel[Frame[FrameContentT]],
        stop_token: StopToken,
    ) -> None:
        logger.debug("produce_frames: started")
        produced_count = 0
        try:
            while True:
                # Checked before the next read, not raced against it: a stop
                # takes effect with at most one already-in-flight read left to
                # finish, same as how a source going dry is only ever noticed
                # between reads.
                if stop_token.is_stop_requested:
                    logger.info(
                        "produce_frames: stop requested after {} frames",
                        produced_count,
                    )
                    break
                frame = await frame_source.read_frame()
                if frame is None:
                    logger.info(
                        "produce_frames: source exhausted after {} frames",
                        produced_count,
                    )
                    break
                logger.trace("produce_frames: read frame {}", frame.index)
                await detection_frame_channel.send(frame)
                await render_frame_channel.send(frame)
                produced_count += 1
        finally:
            # Closed in a `finally`, not just on the happy path: a stage that
            # fails or gets cancelled must still hand its consumers an end of
            # stream, or they sit on an open channel forever and the TaskGroup
            # can never finish shutting down.
            await detection_frame_channel.close()
            await render_frame_channel.close()
        logger.debug("produce_frames: frame channels closed")

    async def sample_and_detect(
        self,
        detection_frame_channel: Channel[Frame[FrameContentT]],
        frame_faces_channel: Channel[list[tuple[Frame[FrameContentT], list[Face[None]]]]],
    ) -> None:
        batch_gate = BatchGate(
            self.clock,
            self.config.frames_per_second,
            self.config.batching.max_frames,
            self.config.batching.max_lag_ms,
        )
        pending_frames: dict[FrameIndex, Frame[FrameContentT]] = {}
        last_sampled_index: FrameIndex | None = None

        def buffer(frame: Frame[FrameContentT]) -> None:
            pending_frames[frame.index] = frame
            if len(pending_frames) > _MAX_PENDING_FRAMES:
                del pending_frames[min(pending_frames)]

        try:
            async for frame in detection_frame_channel:
                buffer(frame)
                # Detection is awaited inline below, so frames keep piling up on
                # the channel while it runs. Take the whole backlog before
                # deciding, rather than one frame per iteration: `_sample_evenly`
                # is meant to spread its sample across the entire interval since
                # the last pass, and consuming the backlog one frame at a time
                # would instead walk it oldest-first — detecting on frames that
                # are already stale and falling permanently further behind the
                # source instead of skipping ahead to what it just read.
                while (queued := detection_frame_channel.try_recv()) is not None:
                    buffer(queued)

                available = sorted(
                    index
                    for index in pending_frames
                    if last_sampled_index is None or index > last_sampled_index
                )
                if not batch_gate.should_fire(len(available)):
                    continue

                sample_indices = _sample_evenly(
                    available, self.config.batching.max_frames
                )
                sampled_frames = [pending_frames[index] for index in sample_indices]

                started_at = self.clock.now()
                faces_by_frame = await self.face_detector.detect_faces(sampled_frames)
                batch_gate.record_spend(self.clock.now() - started_at)

                frame_faces_batch = list(zip(sampled_frames, faces_by_frame, strict=True))
                await frame_faces_channel.send(frame_faces_batch)
                last_sampled_index = sample_indices[-1]
                # `available` only ever looks past the newest sampled index, so
                # everything at or below it is already unreachable. Drop it now
                # rather than leaving it for the _MAX_PENDING_FRAMES cap to
                # evict much later: each entry pins a full raw frame (~2.8 MB at
                # 720x1280), so sitting on 600 of them costs well over a GB.
                for stale_index in [
                    index for index in pending_frames if index <= last_sampled_index
                ]:
                    del pending_frames[stale_index]
        finally:
            await frame_faces_channel.close()

    async def embed_faces(
        self,
        frame_faces_channel: Channel[list[tuple[Frame[FrameContentT], list[Face[None]]]]],
        embedded_snapshots_channel: Channel[list[Snapshot[Face[FaceEmbeddingT]]]],
    ) -> None:
        try:
            async for frame_faces_batch in frame_faces_channel:
                # Faces across every sampled frame in the batch are embedded in
                # one call (real batching for e.g. ONNX inference — the embedder
                # needs each face's originating frame to crop and align from),
                # then re-split back to their originating frame.
                counts = [len(faces) for _, faces in frame_faces_batch]
                faces_with_frames = [
                    (frame, face) for frame, faces in frame_faces_batch for face in faces
                ]
                embedded = await self.face_embedder.embed_faces(faces_with_frames)

                embedded_batch: list[Snapshot[Face[FaceEmbeddingT]]] = []
                offset = 0
                for (frame, _), count in zip(frame_faces_batch, counts, strict=True):
                    embedded_batch.append(
                        Snapshot(
                            frame_index=frame.index,
                            faces=embedded[offset : offset + count],
                        )
                    )
                    offset += count
                await embedded_snapshots_channel.send(embedded_batch)
        finally:
            await embedded_snapshots_channel.close()

    async def track_faces(
        self,
        embedded_snapshots_channel: Channel[list[Snapshot[Face[FaceEmbeddingT]]]],
        tracked_snapshot_channel: Channel[Snapshot[TrackedFace[FaceEmbeddingT]]],
    ) -> None:
        try:
            async for snapshots in embedded_snapshots_channel:
                # Frames within a batch are processed in order — the tracker keeps
                # temporal state (Kalman-style prediction), so calls cannot be
                # reordered or parallelized across frames.
                for snapshot in snapshots:
                    tracked_faces = await self.tracker.update(snapshot.faces)
                    await tracked_snapshot_channel.send(
                        Snapshot(frame_index=snapshot.frame_index, faces=tracked_faces)
                    )
        finally:
            await tracked_snapshot_channel.close()

    async def interpolate_and_render(
        self,
        render_frame_channel: Channel[Frame[FrameContentT]],
        tracked_snapshot_channel: Channel[Snapshot[TrackedFace[FaceEmbeddingT]]],
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, TrackedFace[FaceEmbeddingT]]
        ],
    ) -> None:
        cursor: _RenderCursor[FaceEmbeddingT] = _RenderCursor(
            self.config.rendering.lookahead_snapshots, self.interpolator
        )
        pending_frames: dict[FrameIndex, Frame[FrameContentT]] = {}

        async def collect_snapshots() -> None:
            async for snapshot in tracked_snapshot_channel:
                cursor.push_snapshot(snapshot)

        collector = asyncio.ensure_future(collect_snapshots())
        try:
            async for frame in render_frame_channel:
                pending_frames[frame.index] = frame
                if len(pending_frames) > _MAX_PENDING_FRAMES:
                    del pending_frames[min(pending_frames)]

                result = await cursor.advance()
                if result is None:
                    continue
                rendered_frame = pending_frames.pop(result.frame_index, None)
                # The render cursor only ever moves forward, so any frame older
                # than the one it just asked for is unreachable — same reason as
                # in `sample_and_detect`, same cost for holding on to it.
                for stale_index in [
                    index for index in pending_frames if index < result.frame_index
                ]:
                    del pending_frames[stale_index]
                if rendered_frame is None:
                    # Either the cursor is clamped and re-asked for a frame we
                    # already emitted, or the frame aged out — nothing to render.
                    continue
                await annotated_frame_channel.send(
                    AnnotatedFrame(
                        frame=rendered_frame,
                        faces=result.faces,
                        interpolation_bracket=result.bracket,
                        is_exact=result.is_exact,
                    )
                )
        finally:
            # `collect_snapshots` has no end of its own — it sits on
            # `tracked_snapshot_channel` until that channel closes. Awaiting it
            # bare deadlocks whenever this stage stops first: on our own
            # cancellation (a sibling stage crashed, and the TaskGroup cancelled
            # `track_faces` before it could close the channel) the await swallows
            # the CancelledError and never returns, so the TaskGroup never exits
            # and the original exception is never reported — the whole process
            # just hangs. Cancel it instead; once rendering is over, any further
            # snapshot is unusable anyway.
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector
            await annotated_frame_channel.close()

    async def write_and_broadcast(
        self,
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, TrackedFace[FaceEmbeddingT]]
        ],
    ) -> None:
        async for annotated_frame in annotated_frame_channel:
            await self.frame_sink.write_frame(annotated_frame)
            await self.frame_broadcaster.broadcast_frame(annotated_frame)

    async def run(
        self,
        frame_source: FrameSource[FrameContentT],
        *,
        stop_token: StopToken | None = None,
    ) -> None:
        logger.info("run: starting pipeline")
        stop_token = stop_token if stop_token is not None else StopToken()
        detection_frame_channel: Channel[Frame[FrameContentT]] = Channel(
            name="detection_frames"
        )
        render_frame_channel: Channel[Frame[FrameContentT]] = Channel(
            _FRAME_CHANNEL_CAPACITY, name="render_frames"
        )
        frame_faces_channel: Channel[
            list[tuple[Frame[FrameContentT], list[Face[None]]]]
        ] = Channel(name="frame_faces")
        embedded_snapshots_channel: Channel[list[Snapshot[Face[FaceEmbeddingT]]]] = Channel(
            name="embedded_snapshots"
        )
        tracked_snapshot_channel: Channel[Snapshot[TrackedFace[FaceEmbeddingT]]] = Channel(
            name="tracked_snapshots"
        )
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, TrackedFace[FaceEmbeddingT]]
        ] = Channel(_FRAME_CHANNEL_CAPACITY, name="annotated_frames")
        try:
            async with TaskGroup() as task_group:
                task_group.create_task(
                    self.produce_frames(
                        frame_source,
                        detection_frame_channel,
                        render_frame_channel,
                        stop_token,
                    )
                )
                task_group.create_task(
                    self.sample_and_detect(detection_frame_channel, frame_faces_channel)
                )
                task_group.create_task(
                    self.embed_faces(frame_faces_channel, embedded_snapshots_channel)
                )
                task_group.create_task(
                    self.track_faces(embedded_snapshots_channel, tracked_snapshot_channel)
                )
                task_group.create_task(
                    self.interpolate_and_render(
                        render_frame_channel,
                        tracked_snapshot_channel,
                        annotated_frame_channel,
                    )
                )
                task_group.create_task(self.write_and_broadcast(annotated_frame_channel))
        except Exception:
            logger.exception("run: pipeline failed")
            raise
        logger.info("run: pipeline finished")

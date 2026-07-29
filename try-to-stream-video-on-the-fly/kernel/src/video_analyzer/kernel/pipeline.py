import asyncio
import contextlib
import math
from asyncio import TaskGroup
from dataclasses import replace

from loguru import logger

from .batch_gate import BatchGate
from .channel import Channel
from .config import PipelineConfig
from .render_cursor import RenderCursor
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

# Every channel that carries whole frames is bounded, so a stage slower than
# the source applies backpressure all the way back to the decoder instead of
# quietly accumulating decoded frames. Every queued frame is a full raw image
# (~2.8 MB at 720x1280 BGR24), so an unbounded queue in front of a stage that
# can't keep up reaches gigabytes within a minute and then looks like a hang at
# end of stream, as the pipeline drains a backlog nobody knew was there.
#
# Roughly a second of video at 30 fps: enough to absorb scheduling jitter,
# small enough to keep in-flight frames to a couple of hundred MB. It doesn't
# need to cover the interpolation lookahead — that buffering happens in
# `pending_frames`, downstream of this channel.
#
# The detection-frame channel gets extra headroom on top of this (see
# `_detection_channel_capacity`): frames legitimately pile up on it while a
# detection pass is awaited inline, and its bound must not be what throttles a
# normally-paced pass — only a genuinely stuck detector should ever push back
# on the producer (and through it, the render path, since the producer fans
# out to both).
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
        previous_frame: Frame[FrameContentT] | None = None
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
                if previous_frame is not None and await self.scene_detector.detect_scene_cut(
                    previous_frame, frame
                ):
                    logger.info(
                        "produce_frames: scene cut detected at frame {}", frame.index
                    )
                    frame = replace(frame, is_scene_start=True)
                previous_frame = frame
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
            if frame.is_scene_start:
                # A detection batch must never span a scene cut: whatever
                # pre-cut frames are still waiting to be sampled are obsolete —
                # their detections would be tracked into the new scene.
                for pre_cut_index in [
                    index for index in pending_frames if index < frame.index
                ]:
                    del pending_frames[pre_cut_index]
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
                            is_scene_start=frame.is_scene_start,
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
                    if snapshot.is_scene_start:
                        # Identities never survive a scene cut.
                        await self.tracker.reset()
                    tracked_faces = await self.tracker.update(snapshot.faces)
                    await tracked_snapshot_channel.send(
                        Snapshot(
                            frame_index=snapshot.frame_index,
                            faces=tracked_faces,
                            is_scene_start=snapshot.is_scene_start,
                        )
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
        cursor: RenderCursor[FaceEmbeddingT] = RenderCursor(
            self.config.rendering.lookahead_snapshots, self.interpolator
        )
        pending_frames: dict[FrameIndex, Frame[FrameContentT]] = {}
        # The last faces actually emitted — held frames (ones the cursor cannot
        # interpolate: the tail past the final snapshot, or a frame the cursor
        # skipped past) reuse these so annotations freeze rather than vanish.
        held_faces: list[TrackedFace[FaceEmbeddingT]] = []

        async def emit_held(frame: Frame[FrameContentT]) -> None:
            await annotated_frame_channel.send(
                AnnotatedFrame(
                    frame=frame,
                    faces=held_faces,
                    interpolation_bracket=None,
                    is_exact=False,
                )
            )

        async def drain_cursor() -> None:
            """Emit every frame the cursor can currently reach — nothing while
            detections stall, a catch-up burst once they land. Every input
            frame comes out exactly once (short of the `_MAX_PENDING_FRAMES`
            eviction): the cursor walks indices sequentially without duplicates
            or gaps, and any pending frame it somehow got ahead of is emitted
            as held rather than dropped."""
            nonlocal held_faces
            while (result := await cursor.advance()) is not None:
                for skipped_index in sorted(
                    index for index in pending_frames if index < result.frame_index
                ):
                    await emit_held(pending_frames.pop(skipped_index))
                rendered_frame = pending_frames.pop(result.frame_index, None)
                if rendered_frame is None:
                    # Aged out via the pending cap — nothing left to render.
                    continue
                held_faces = result.faces
                await annotated_frame_channel.send(
                    AnnotatedFrame(
                        frame=rendered_frame,
                        faces=result.faces,
                        interpolation_bracket=result.bracket,
                        is_exact=result.is_exact,
                    )
                )

        async def collect_snapshots() -> None:
            async for snapshot in tracked_snapshot_channel:
                cursor.push_snapshot(snapshot)

        collector = asyncio.ensure_future(collect_snapshots())
        try:
            async for frame in render_frame_channel:
                pending_frames[frame.index] = frame
                if len(pending_frames) > _MAX_PENDING_FRAMES:
                    del pending_frames[min(pending_frames)]
                await drain_cursor()

            # Clean end of stream. The upstream stages close their channels in
            # `finally` even when stopping early, so the collector is
            # guaranteed to finish once the trailing detection batches have
            # flowed through — wait for them, then flush: first everything the
            # remaining snapshots can still interpolate (lookahead margin
            # dropped), then the tail beyond the last snapshot with its
            # annotations held. Without this the whole lookahead window of
            # frames silently vanished at end of stream.
            await collector
            cursor.finish()
            await drain_cursor()
            for tail_index in sorted(pending_frames):
                await emit_held(pending_frames.pop(tail_index))
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

    def _detection_channel_capacity(self) -> int:
        """Bound for the detection-frame channel, derived from the batching
        knobs: room for one full batch, plus every frame the source can emit
        while the gate deliberately waits out `max_lag_ms`, plus the shared
        jitter headroom. A healthy detector never fills this; a stuck one hits
        the bound and pushes back on the producer instead of banking an
        unbounded backlog of raw frames."""
        lag_frames = math.ceil(
            self.config.frames_per_second * self.config.batching.max_lag_ms / 1000.0
        )
        return (
            self.config.batching.max_frames + lag_frames + _FRAME_CHANNEL_CAPACITY
        )

    async def run(
        self,
        frame_source: FrameSource[FrameContentT],
        *,
        stop_token: StopToken | None = None,
    ) -> None:
        logger.info("run: starting pipeline")
        stop_token = stop_token if stop_token is not None else StopToken()
        detection_frame_channel: Channel[Frame[FrameContentT]] = Channel(
            self._detection_channel_capacity(), name="detection_frames"
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

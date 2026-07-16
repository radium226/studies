from asyncio import TaskGroup

from loguru import logger

from .batch_gate import BatchGate
from .channel import Channel
from .config import PipelineConfig
from .models import AnnotatedFrame, Face, Frame, FrameIndex, Snapshot
from .services import (
    Clock,
    FaceDetector,
    FaceEmbedder,
    FrameSink,
    FrameSource,
    SceneDetector,
)


def sample_evenly[ItemT](items: list[ItemT], max_count: int) -> list[ItemT]:
    if len(items) <= max_count:
        return list(items)
    if max_count <= 1:
        return [items[-1]]
    sampled_positions = sorted(
        {
            round(sample_index * (len(items) - 1) / (max_count - 1))
            for sample_index in range(max_count)
        }
    )
    return [items[position] for position in sampled_positions]


class Pipeline[FrameContentT, FaceEmbeddingT]:

    clock: Clock
    scene_detector: SceneDetector[FrameContentT]
    face_detector: FaceDetector[FrameContentT]
    face_embedder: FaceEmbedder[FaceEmbeddingT]
    config: PipelineConfig

    def __init__(
        self,
        clock: Clock,
        scene_detector: SceneDetector[FrameContentT],
        face_detector: FaceDetector[FrameContentT],
        face_embedder: FaceEmbedder[FaceEmbeddingT],
        *,
        config: PipelineConfig | None = None,
    ) -> None:
        self.clock = clock
        self.scene_detector = scene_detector
        self.face_detector = face_detector
        self.face_embedder = face_embedder
        self.config = config if config is not None else PipelineConfig()
        logger.debug("Pipeline configured: {}", self.config)

    async def produce_frames(
        self,
        frame_source: FrameSource[FrameContentT],
        frame_channel: Channel[Frame[FrameContentT]],
    ) -> None:
        logger.debug("produce_frames: started")
        produced_count = 0
        while True:
            frame = frame_source.read_frame()
            if frame is None:
                logger.info(
                    "produce_frames: source exhausted after {} frames", produced_count
                )
                break
            logger.trace("produce_frames: read frame {}", frame.index)
            await frame_channel.send(frame)
            produced_count += 1
        await frame_channel.close()
        logger.debug("produce_frames: frame channel closed")

    async def detect_frames(
        self,
        frame_channel: Channel[Frame[FrameContentT]],
        snapshot_channel: Channel[
            Frame[FrameContentT] | Snapshot[Face[FaceEmbeddingT]]
        ],
        batch_gate: BatchGate,
    ) -> None:
        logger.debug("detect_frames: started")
        candidate_frames: list[Frame[FrameContentT]] = []
        previous_frame: Frame[FrameContentT] | None = None
        seen_count = 0
        batch_count = 0
        scene_cut_count = 0
        async for frame in frame_channel:
            seen_count += 1
            logger.trace(
                "detect_frames: frame {} received ({} candidates pending)",
                frame.index,
                len(candidate_frames),
            )
            await snapshot_channel.send(frame)
            scene_cut_detected = (
                previous_frame is not None
                and self.scene_detector.detect_scene_cut(previous_frame, frame)
            )
            if scene_cut_detected and candidate_frames:
                scene_cut_count += 1
                logger.debug(
                    "detect_frames: scene cut at frame {} — flushing {} candidate "
                    "frames before the cut",
                    frame.index,
                    len(candidate_frames),
                )
                await self.detect_batch(candidate_frames, snapshot_channel, batch_gate)
                batch_count += 1
                candidate_frames.clear()
                batch_gate.reset()
            candidate_frames.append(frame)
            previous_frame = frame
            if batch_gate.should_fire(len(candidate_frames)):
                logger.debug(
                    "detect_frames: batch gate fired at frame {} with {} candidates",
                    frame.index,
                    len(candidate_frames),
                )
                await self.detect_batch(candidate_frames, snapshot_channel, batch_gate)
                batch_count += 1
                candidate_frames.clear()
        if candidate_frames:
            logger.debug(
                "detect_frames: channel closed — flushing final {} candidate frames",
                len(candidate_frames),
            )
            await self.detect_batch(candidate_frames, snapshot_channel, batch_gate)
            batch_count += 1
        await snapshot_channel.close()
        logger.info(
            "detect_frames: done ({} frames seen, {} batches, {} scene cuts)",
            seen_count,
            batch_count,
            scene_cut_count,
        )

    async def detect_batch(
        self,
        candidate_frames: list[Frame[FrameContentT]],
        snapshot_channel: Channel[
            Frame[FrameContentT] | Snapshot[Face[FaceEmbeddingT]]
        ],
        batch_gate: BatchGate,
    ) -> None:
        sampled_frames = sample_evenly(candidate_frames, batch_gate.max_batch_frames)
        logger.debug(
            "detect_batch: sampled {} of {} candidates (frames {}..{})",
            len(sampled_frames),
            len(candidate_frames),
            sampled_frames[0].index,
            sampled_frames[-1].index,
        )
        detection_started_at = self.clock.now()
        faces_per_frame = self.face_detector.detect_faces(sampled_frames)
        detection_elapsed = self.clock.now() - detection_started_at
        batch_gate.record_spend(detection_elapsed)
        total_faces = sum(len(faces) for faces in faces_per_frame)
        logger.debug(
            "detect_batch: detection took {:.1f} ms, found {} faces across {} frames",
            detection_elapsed * 1000.0,
            total_faces,
            len(sampled_frames),
        )
        for frame, faces in zip(sampled_frames, faces_per_frame, strict=True):
            embedded_faces = self.face_embedder.embed_faces(faces)
            logger.trace(
                "detect_batch: snapshot for frame {} with {} embedded faces",
                frame.index,
                len(embedded_faces),
            )
            await snapshot_channel.send(
                Snapshot(frame_index=frame.index, detections=embedded_faces)
            )

    async def render_frames(
        self,
        snapshot_channel: Channel[
            Frame[FrameContentT] | Snapshot[Face[FaceEmbeddingT]]
        ],
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, Face[FaceEmbeddingT]]
        ],
    ) -> None:
        logger.debug("render_frames: started")
        pending_frames: dict[FrameIndex, Frame[FrameContentT]] = {}
        snapshots: list[Snapshot[Face[FaceEmbeddingT]]] = []
        emitted_count = 0
        exact_count = 0
        flushed_count = 0

        def find_segment(frame_index: FrameIndex) -> int | None:
            for segment_index in range(len(snapshots) - 1):
                earlier_snapshot = snapshots[segment_index]
                later_snapshot = snapshots[segment_index + 1]
                if (
                    earlier_snapshot.frame_index
                    <= frame_index
                    <= later_snapshot.frame_index
                ):
                    return segment_index
            return None

        def annotate(
            frame: Frame[FrameContentT],
            segment_index: int | None,
            flushed: bool,
        ) -> AnnotatedFrame[FrameContentT, Face[FaceEmbeddingT]]:
            nonlocal emitted_count, exact_count, flushed_count
            emitted_count += 1
            if flushed:
                flushed_count += 1
            if segment_index is None:
                logger.trace(
                    "render_frames: frame {} emitted without bracket (flushed={})",
                    frame.index,
                    flushed,
                )
                return AnnotatedFrame(
                    frame=frame, bracket=None, is_exact=False, flushed=flushed
                )
            earlier_snapshot = snapshots[segment_index]
            later_snapshot = snapshots[segment_index + 1]
            is_exact = frame.index in (
                earlier_snapshot.frame_index,
                later_snapshot.frame_index,
            )
            if is_exact:
                exact_count += 1
            logger.trace(
                "render_frames: frame {} emitted with bracket [{}, {}] "
                "(exact={}, flushed={})",
                frame.index,
                earlier_snapshot.frame_index,
                later_snapshot.frame_index,
                is_exact,
                flushed,
            )
            return AnnotatedFrame(
                frame=frame,
                bracket=(earlier_snapshot, later_snapshot),
                is_exact=is_exact,
                flushed=flushed,
            )

        async def emit_ready() -> None:
            while pending_frames:
                oldest_frame_index = next(iter(pending_frames))
                segment_index = find_segment(oldest_frame_index)
                if segment_index is None:
                    logger.trace(
                        "render_frames: frame {} not bracketed yet "
                        "({} snapshots, {} pending frames)",
                        oldest_frame_index,
                        len(snapshots),
                        len(pending_frames),
                    )
                    return
                if len(snapshots) - (segment_index + 2) < self.config.rendering.lookahead_snapshots:
                    logger.trace(
                        "render_frames: frame {} waiting for lookahead "
                        "({} snapshots ahead, {} required)",
                        oldest_frame_index,
                        len(snapshots) - (segment_index + 2),
                        self.config.rendering.lookahead_snapshots,
                    )
                    return
                frame = pending_frames.pop(oldest_frame_index)
                await annotated_frame_channel.send(
                    annotate(frame, segment_index, flushed=False)
                )
                while (
                    len(snapshots) >= 2
                    and snapshots[1].frame_index < oldest_frame_index + 1
                ):
                    dropped_snapshot = snapshots.pop(0)
                    logger.trace(
                        "render_frames: dropped stale snapshot for frame {}",
                        dropped_snapshot.frame_index,
                    )

        async for message in snapshot_channel:
            match message:
                case Frame() as frame:
                    pending_frames[frame.index] = frame
                    logger.trace(
                        "render_frames: buffered frame {} ({} pending)",
                        frame.index,
                        len(pending_frames),
                    )
                case Snapshot() as snapshot:
                    snapshots.append(snapshot)
                    logger.trace(
                        "render_frames: snapshot for frame {} with {} detections "
                        "({} snapshots held)",
                        snapshot.frame_index,
                        len(snapshot.detections),
                        len(snapshots),
                    )
            await emit_ready()

        if pending_frames:
            logger.debug(
                "render_frames: channel closed — flushing {} pending frames",
                len(pending_frames),
            )
        for frame_index, frame in pending_frames.items():
            await annotated_frame_channel.send(
                annotate(frame, find_segment(frame_index), flushed=True)
            )
        pending_frames.clear()
        await annotated_frame_channel.close()
        logger.info(
            "render_frames: done ({} frames emitted: {} exact, {} flushed)",
            emitted_count,
            exact_count,
            flushed_count,
        )

    async def consume_frames(
        self,
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, Face[FaceEmbeddingT]]
        ],
        frame_sink: FrameSink[FrameContentT],
    ) -> None:
        logger.debug("consume_frames: started")
        consumed_count = 0
        async for annotated_frame in annotated_frame_channel:
            logger.trace(
                "consume_frames: writing frame {} to sink",
                annotated_frame.frame.index,
            )
            frame_sink.write_frame(annotated_frame.frame)
            consumed_count += 1
        logger.info("consume_frames: done ({} frames written)", consumed_count)

    async def drain(
        self,
        frame_source: FrameSource[FrameContentT],
        frame_sink: FrameSink[FrameContentT],
    ) -> None:
        logger.info("drain: starting pipeline")
        frame_channel: Channel[Frame[FrameContentT]] = Channel(name="frames")
        snapshot_channel: Channel[
            Frame[FrameContentT] | Snapshot[Face[FaceEmbeddingT]]
        ] = Channel(name="snapshots")
        annotated_frame_channel: Channel[
            AnnotatedFrame[FrameContentT, Face[FaceEmbeddingT]]
        ] = Channel(name="annotated-frames")
        batch_gate = BatchGate(
            clock=self.clock,
            frames_per_second=self.config.frames_per_second,
            max_batch_frames=self.config.batching.max_frames,
            max_batch_lag_ms=self.config.batching.max_lag_ms,
        )
        try:
            async with TaskGroup() as task_group:
                task_group.create_task(
                    self.produce_frames(frame_source, frame_channel)
                )
                task_group.create_task(
                    self.detect_frames(frame_channel, snapshot_channel, batch_gate)
                )
                task_group.create_task(
                    self.render_frames(snapshot_channel, annotated_frame_channel)
                )
                task_group.create_task(
                    self.consume_frames(annotated_frame_channel, frame_sink)
                )
        except Exception:
            logger.exception("drain: pipeline failed")
            raise
        logger.info("drain: pipeline finished")

from asyncio import TaskGroup
from dataclasses import dataclass
from typing import NamedTuple

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


class FrameAndFacesWithoutEmbedding[FrameContentT](NamedTuple):
    frame: Frame[FrameContentT]
    faces: list[Face[None]]


class FrameAndFacesWithEmbedding[FrameContentT, FaceEmbeddingT](NamedTuple):
    frame: Frame[FrameContentT]
    faces: list[Face[FaceEmbeddingT]]


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

    
    async def detect_faces(
        self,
        frame_channel: Channel[Frame[FrameContentT]],
        frame_and_faces_without_embedding_channel: Channel[FrameAndFacesWithoutEmbedding[FrameContentT]],
    ) -> None:
        async for frame in frame_channel:
            logger.trace("detect_faces: received frame {}", frame.index)
            faces = self.face_detector.detect_faces([frame])[0]
            logger.trace(
                "detect_faces: detected {} faces in frame {}", len(faces), frame.index
            )
            await frame_and_faces_without_embedding_channel.send(
                FrameAndFacesWithoutEmbedding(frame=frame, faces=faces)
            )

        await frame_and_faces_without_embedding_channel.close()


    async def embed_faces(
        self,
        frame_and_faces_without_embedding_channel: Channel[FrameAndFacesWithoutEmbedding[FrameContentT]],
        frame_and_faces_with_embedding_channel: Channel[FrameAndFacesWithEmbedding[FrameContentT, FaceEmbeddingT]],
    ) -> None:
        async for frame, faces_without_embedding in frame_and_faces_without_embedding_channel:
            logger.trace("embed_faces: received frame {}", frame.index)
            faces_with_embedding = self.face_embedder.embed_faces(faces_without_embedding)
            logger.trace(
                "embed_faces: embedded {} faces in frame {}", len(faces_with_embedding), frame.index
            )
            await frame_and_faces_with_embedding_channel.send(
                FrameAndFacesWithEmbedding(frame=frame, faces=faces_with_embedding)
            )

        await frame_and_faces_with_embedding_channel.close()


    async def drain(
        self,
        frame_source: FrameSource[FrameContentT],
    ) -> None:
        logger.info("drain: starting pipeline")
        frame_channel: Channel[Frame[FrameContentT]] = Channel(name="frames")
        frame_and_faces_without_embedding_channel: Channel[FrameAndFacesWithoutEmbedding[FrameContentT]] = Channel(name="frame_and_faces_without_embedding")
        frame_and_faces_with_embedding_channel: Channel[FrameAndFacesWithEmbedding[FrameContentT, FaceEmbeddingT]] = Channel(name="frame_and_faces_with_embedding")
        try:
            async with TaskGroup() as task_group:
                task_group.create_task(
                    self.produce_frames(
                        frame_source, 
                        frame_channel,
                    )
                )

                task_group.create_task(
                    self.detect_faces(
                        frame_channel,
                        frame_and_faces_without_embedding_channel,
                    )
                )

                task_group.create_task(
                    self.embed_faces(
                        frame_and_faces_without_embedding_channel,
                        frame_and_faces_with_embedding_channel,
                    )
                )
        except Exception:
            logger.exception("drain: pipeline failed")
            raise
        logger.info("drain: pipeline finished")

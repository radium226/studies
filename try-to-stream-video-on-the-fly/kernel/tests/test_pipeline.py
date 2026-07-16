import asyncio

from .fake import (
    Clock,
    FaceDetector,
    FaceEmbedder,
    Frame,
    FrameSink,
    FrameSource,
    Pipeline,
    SceneDetector,
)


def test_pipeline() -> None:
    pipeline = Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(),
        face_detector=FaceDetector(),
        face_embedder=FaceEmbedder(),
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(10)]
    frame_source = FrameSource(source_frames)
    frame_sink = FrameSink()

    asyncio.run(pipeline.drain(frame_source, frame_sink))

    assert frame_sink.written_frames == source_frames

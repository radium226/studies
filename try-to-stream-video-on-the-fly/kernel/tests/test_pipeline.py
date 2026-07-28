import asyncio

from video_analyzer.kernel import BatchingConfig, PipelineConfig, RenderingConfig

from .fake import (
    Clock,
    FaceDetector,
    FaceEmbedder,
    Frame,
    FrameBroadcaster,
    FrameSink,
    FrameSource,
    Interpolator,
    Pipeline,
    SceneDetector,
    Tracker,
)


def test_pipeline() -> None:
    pipeline = Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(),
        face_detector=FaceDetector(),
        face_embedder=FaceEmbedder(),
        tracker=Tracker(),
        interpolator=Interpolator(),
        frame_sink=(frame_sink := FrameSink()),
        frame_broadcaster=(frame_broadcaster := FrameBroadcaster()),
        config=PipelineConfig(
            frames_per_second=30.0,
            batching=BatchingConfig(max_frames=4, max_lag_ms=0.0),
            rendering=RenderingConfig(lookahead_snapshots=1),
        ),
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(20)]
    frame_source = FrameSource(source_frames)

    asyncio.run(pipeline.drain(frame_source))

    # Detection runs sparsely and interpolation lags by design (it needs
    # future snapshots to avoid extrapolating), so not every source frame is
    # guaranteed to make it out the other end — but at least some should.
    assert 0 < len(frame_sink.written_frames) <= len(source_frames)
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)

    for annotated_frame in frame_sink.written_frames:
        assert annotated_frame.bracket is not None
        assert len(annotated_frame.detections) == 1
        assert annotated_frame.detections[0].track_id == 0

    # Frames are emitted in the order the render cursor advances through them.
    rendered_indices = [af.frame.index for af in frame_sink.written_frames]
    assert rendered_indices == sorted(rendered_indices)

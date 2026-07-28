import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

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


def _run_until(
    coroutine_factory: Callable[[], Coroutine[Any, Any, None]],
    timeout: float = 10.0,
) -> BaseException | None:
    """Run a coroutine on its own thread and return whatever it raised.

    A daemon thread rather than `asyncio.timeout`, because the deadlock this
    guards against swallows cancellation: an in-loop timeout could not break out
    of it either, so a regression would hang the whole suite instead of failing
    one test.
    """
    raised: dict[str, BaseException] = {}

    def target() -> None:
        try:
            asyncio.run(coroutine_factory())
        except BaseException as exception:  # noqa: BLE001 - reported to the caller
            raised["exception"] = exception

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"drain() still running after {timeout}s"
    return raised.get("exception")


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


def test_pipeline_reports_a_failing_stage_instead_of_hanging() -> None:
    """A crash in any stage has to come back out of `drain`.

    Regression test: `interpolate_and_render` used to `await` its snapshot
    collector bare in a `finally`, and that collector sat on a channel `track`
    never got to close once the TaskGroup began cancelling. The CancelledError
    was swallowed, the TaskGroup never exited, and the real exception was never
    reported — the process just hung forever.
    """

    class ExplodingFrameSink(FrameSink):
        async def write_frame(self, annotated_frame: Any) -> None:
            raise RuntimeError("sink exploded")

    pipeline = Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(),
        face_detector=FaceDetector(),
        face_embedder=FaceEmbedder(),
        tracker=Tracker(),
        interpolator=Interpolator(),
        frame_sink=ExplodingFrameSink(),
        frame_broadcaster=FrameBroadcaster(),
        config=PipelineConfig(
            frames_per_second=30.0,
            batching=BatchingConfig(max_frames=4, max_lag_ms=0.0),
            rendering=RenderingConfig(lookahead_snapshots=1),
        ),
    )
    # Endless source: the failure must end the run on its own, not merely
    # coincide with the source running dry.
    frame_source = FrameSource([Frame(index=index, content=index * 10) for index in range(10_000)])

    raised = _run_until(lambda: pipeline.drain(frame_source))

    assert isinstance(raised, BaseExceptionGroup)
    runtime_errors = [
        exception
        for exception in raised.exceptions
        if isinstance(exception, RuntimeError)
    ]
    assert [str(exception) for exception in runtime_errors] == ["sink exploded"]

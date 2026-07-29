import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from video_analyzer.kernel import BatchingConfig, PipelineConfig, RenderingConfig, StopToken

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
    assert not thread.is_alive(), f"run() still running after {timeout}s"
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

    asyncio.run(pipeline.run(frame_source))

    # Detection runs sparsely and interpolation lags by design (it needs
    # future snapshots to avoid extrapolating), so not every source frame is
    # guaranteed to make it out the other end — but at least some should.
    assert 0 < len(frame_sink.written_frames) <= len(source_frames)
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)

    for annotated_frame in frame_sink.written_frames:
        assert annotated_frame.interpolation_bracket is not None
        assert len(annotated_frame.faces) == 1
        assert annotated_frame.faces[0].track_id == 0

    # Frames are emitted in the order the render cursor advances through them.
    rendered_indices = [
        annotated_frame.frame.index for annotated_frame in frame_sink.written_frames
    ]
    assert rendered_indices == sorted(rendered_indices)


def test_pipeline_reports_a_failing_stage_instead_of_hanging() -> None:
    """A crash in any stage has to come back out of `run`.

    Regression test: `interpolate_and_render` used to `await` its snapshot
    collector bare in a `finally`, and that collector sat on a channel
    `track_faces` never got to close once the TaskGroup began cancelling. The
    CancelledError
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
    # coincide with the source running dry. This is deliberately a different
    # code path from `StopToken` (TaskGroup exception propagation vs a
    # graceful, caller-requested stop), so it's not converted to use one.
    frame_source = FrameSource([Frame(index=index, content=index * 10) for index in range(10_000)])

    raised = _run_until(lambda: pipeline.run(frame_source))

    assert isinstance(raised, BaseExceptionGroup)
    runtime_errors = [
        exception
        for exception in raised.exceptions
        if isinstance(exception, RuntimeError)
    ]
    assert [str(exception) for exception in runtime_errors] == ["sink exploded"]


def _make_pipeline(
    frame_sink: FrameSink, frame_broadcaster: FrameBroadcaster
) -> Pipeline:
    return Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(),
        face_detector=FaceDetector(),
        face_embedder=FaceEmbedder(),
        tracker=Tracker(),
        interpolator=Interpolator(),
        frame_sink=frame_sink,
        frame_broadcaster=frame_broadcaster,
        config=PipelineConfig(
            frames_per_second=30.0,
            batching=BatchingConfig(max_frames=4, max_lag_ms=0.0),
            rendering=RenderingConfig(lookahead_snapshots=1),
        ),
    )


def test_stop_token_set_before_any_frame_reads_short_circuits_run() -> None:
    pipeline = _make_pipeline(
        frame_sink := FrameSink(), frame_broadcaster := FrameBroadcaster()
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(20)]
    frame_source = FrameSource(source_frames)
    stop_token = StopToken()
    stop_token.request_stop()

    asyncio.run(pipeline.run(frame_source, stop_token=stop_token))

    assert frame_source.remaining_frames == source_frames
    assert frame_sink.written_frames == []
    assert frame_broadcaster.broadcast_frames == []


def test_stop_token_set_mid_stream_still_drains_already_read_frames() -> None:
    """Graceful stop, not a hard cut: frames already read before the stop keep
    flowing all the way to the sink/broadcaster, and nothing past the stop
    point is ever read."""

    stop_token = StopToken()
    stop_after_index = 15

    class StopAfterFewFrames(FrameSource):
        async def read_frame(self) -> Frame | None:
            frame = await super().read_frame()
            if frame is not None and frame.index == stop_after_index:
                stop_token.request_stop()
            return frame

    frame_source = StopAfterFewFrames(
        [Frame(index=index, content=index * 10) for index in range(10_000)]
    )
    pipeline = _make_pipeline(
        frame_sink := FrameSink(), frame_broadcaster := FrameBroadcaster()
    )

    asyncio.run(pipeline.run(frame_source, stop_token=stop_token))

    assert len(frame_source.remaining_frames) == 10_000 - (stop_after_index + 1)
    assert 0 < len(frame_sink.written_frames)
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)
    assert max(annotated_frame.frame.index for annotated_frame in frame_sink.written_frames) <= stop_after_index


def test_natural_eof_unaffected_by_an_unset_stop_token() -> None:
    """Regression guard for the new `stop_token` parameter itself: passing one
    that's never triggered must behave exactly like passing none at all."""
    pipeline = _make_pipeline(
        frame_sink := FrameSink(), frame_broadcaster := FrameBroadcaster()
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(20)]
    frame_source = FrameSource(source_frames)

    asyncio.run(pipeline.run(frame_source, stop_token=StopToken()))

    assert 0 < len(frame_sink.written_frames) <= len(source_frames)
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)

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

    # Every input frame comes out exactly once, in order: detection runs
    # sparsely and interpolation lags, but the render cursor catches up in
    # bursts and the end-of-stream flush drains the lookahead tail.
    rendered_indices = [
        annotated_frame.frame.index for annotated_frame in frame_sink.written_frames
    ]
    assert rendered_indices == [frame.index for frame in source_frames]
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)

    for annotated_frame in frame_sink.written_frames:
        assert len(annotated_frame.faces) == 1
        assert annotated_frame.faces[0].track_id == 0
        if annotated_frame.interpolation_bracket is None:
            # Held frame: only the flushed tail past the last detection
            # snapshot may lack a bracket.
            assert annotated_frame.is_exact is False


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
    # Every frame read before the stop still comes out, exactly once, in order.
    assert [
        annotated_frame.frame.index for annotated_frame in frame_sink.written_frames
    ] == list(range(stop_after_index + 1))
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)


def test_natural_eof_unaffected_by_an_unset_stop_token() -> None:
    """Regression guard for the new `stop_token` parameter itself: passing one
    that's never triggered must behave exactly like passing none at all."""
    pipeline = _make_pipeline(
        frame_sink := FrameSink(), frame_broadcaster := FrameBroadcaster()
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(20)]
    frame_source = FrameSource(source_frames)

    asyncio.run(pipeline.run(frame_source, stop_token=StopToken()))

    assert len(frame_sink.written_frames) == len(source_frames)
    assert len(frame_broadcaster.broadcast_frames) == len(frame_sink.written_frames)


def test_scene_cut_resets_track_identities_and_loses_no_frames() -> None:
    """A scene cut must reset the tracker (the fake shifts its ids by 100 per
    reset) and never let annotations interpolate across the cut — while the
    every-frame-out-exactly-once invariant still holds."""

    cut_index = 20
    pipeline = Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(cut_at={cut_index}),
        face_detector=FaceDetector(),
        face_embedder=FaceEmbedder(),
        tracker=Tracker(),
        interpolator=Interpolator(),
        frame_sink=(frame_sink := FrameSink()),
        frame_broadcaster=FrameBroadcaster(),
        config=PipelineConfig(
            frames_per_second=30.0,
            batching=BatchingConfig(max_frames=4, max_lag_ms=0.0),
            rendering=RenderingConfig(lookahead_snapshots=1),
        ),
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(40)]

    asyncio.run(pipeline.run(FrameSource(source_frames)))

    assert [
        annotated_frame.frame.index for annotated_frame in frame_sink.written_frames
    ] == [frame.index for frame in source_frames]
    for annotated_frame in frame_sink.written_frames:
        track_ids = {face.track_id for face in annotated_frame.faces}
        if annotated_frame.frame.index < cut_index:
            assert track_ids <= {0}
        else:
            assert track_ids <= {100}
        if annotated_frame.interpolation_bracket is not None:
            segment_start, segment_end = annotated_frame.interpolation_bracket
            assert (segment_start.frame_index < cut_index) == (
                segment_end.frame_index < cut_index
            )


def test_all_frames_emitted_even_when_detection_stalls_mid_stream() -> None:
    """The render cursor must never drop frames while detections are late: it
    waits, then catches up in a burst once the next snapshots land."""

    class StallingFaceDetector(FaceDetector):
        def __init__(self) -> None:
            self.detect_calls = 0

        async def detect_faces(self, frame_batch: Any) -> Any:
            self.detect_calls += 1
            if self.detect_calls == 2:
                # Let a long stretch of frames flow past while this detection
                # pass is "busy" — the backlog must come out the other end.
                for _ in range(500):
                    await asyncio.sleep(0)
            return await super().detect_faces(frame_batch)

    pipeline = Pipeline(
        clock=Clock(),
        scene_detector=SceneDetector(),
        face_detector=StallingFaceDetector(),
        face_embedder=FaceEmbedder(),
        tracker=Tracker(),
        interpolator=Interpolator(),
        frame_sink=(frame_sink := FrameSink()),
        frame_broadcaster=FrameBroadcaster(),
        config=PipelineConfig(
            frames_per_second=30.0,
            batching=BatchingConfig(max_frames=4, max_lag_ms=0.0),
            rendering=RenderingConfig(lookahead_snapshots=1),
        ),
    )
    source_frames = [Frame(index=index, content=index * 10) for index in range(60)]

    asyncio.run(pipeline.run(FrameSource(source_frames)))

    assert [
        annotated_frame.frame.index for annotated_frame in frame_sink.written_frames
    ] == [frame.index for frame in source_frames]

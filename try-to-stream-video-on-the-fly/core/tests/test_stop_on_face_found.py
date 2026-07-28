from video_analyzer import core, kernel


class _RecordingFrameBroadcaster(kernel.FrameBroadcaster[int, int]):
    def __init__(self) -> None:
        self.broadcast_frames: list[kernel.AnnotatedFrame[int, int]] = []

    async def broadcast_frame(
        self, annotated_frame: kernel.AnnotatedFrame[int, int]
    ) -> None:
        self.broadcast_frames.append(annotated_frame)


def _annotated_frame(index: int, detections: list[int]) -> kernel.AnnotatedFrame[int, int]:
    return kernel.AnnotatedFrame(
        frame=kernel.Frame(index=index, content=index),
        detections=detections,
        bracket=None,
        is_exact=True,
    )


async def test_no_stop_when_no_detections() -> None:
    wrapped = _RecordingFrameBroadcaster()
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFaceFound(wrapped, stop_token)
    frame = _annotated_frame(0, [])

    await broadcaster.broadcast_frame(frame)

    assert not stop_token.is_stop_requested
    assert wrapped.broadcast_frames == [frame]


async def test_stop_requested_on_first_frame_with_a_detection() -> None:
    wrapped = _RecordingFrameBroadcaster()
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFaceFound(wrapped, stop_token)

    await broadcaster.broadcast_frame(_annotated_frame(0, []))
    assert not stop_token.is_stop_requested

    await broadcaster.broadcast_frame(_annotated_frame(1, [42]))
    assert stop_token.is_stop_requested


async def test_every_frame_still_delegates_to_the_wrapped_broadcaster() -> None:
    wrapped = _RecordingFrameBroadcaster()
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFaceFound(wrapped, stop_token)
    frames = [_annotated_frame(0, []), _annotated_frame(1, [42]), _annotated_frame(2, [7])]

    for frame in frames:
        await broadcaster.broadcast_frame(frame)

    assert wrapped.broadcast_frames == frames

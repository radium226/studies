from video_analyzer import core, kernel


class _ListFrameSource(kernel.FrameSource[int]):
    def __init__(self, contents: list[int]) -> None:
        self._contents = list(contents)
        self._next_index = 0

    async def read_frame(self) -> kernel.Frame[int] | None:
        if not self._contents:
            return None
        content = self._contents.pop(0)
        frame = kernel.Frame(index=self._next_index, content=content)
        self._next_index += 1
        return frame


async def test_requests_stop_once_max_frames_read() -> None:
    stop_token = kernel.StopToken()
    source = core.StopAfterFrameCount(
        _ListFrameSource([10, 20, 30]),
        stop_token,
        config=core.StopAfterFrameCountConfig(max_frames=2),
    )

    assert (await source.read_frame()) is not None
    assert not stop_token.is_stop_requested

    assert (await source.read_frame()) is not None
    assert stop_token.is_stop_requested


async def test_nth_frame_is_still_returned_not_swallowed() -> None:
    stop_token = kernel.StopToken()
    source = core.StopAfterFrameCount(
        _ListFrameSource([10, 20]),
        stop_token,
        config=core.StopAfterFrameCountConfig(max_frames=1),
    )

    frame = await source.read_frame()

    assert frame is not None
    assert frame.content == 10
    assert stop_token.is_stop_requested


async def test_source_exhausted_before_max_frames_never_triggers_stop() -> None:
    stop_token = kernel.StopToken()
    source = core.StopAfterFrameCount(
        _ListFrameSource([10]),
        stop_token,
        config=core.StopAfterFrameCountConfig(max_frames=5),
    )

    assert (await source.read_frame()) is not None
    assert (await source.read_frame()) is None
    assert not stop_token.is_stop_requested

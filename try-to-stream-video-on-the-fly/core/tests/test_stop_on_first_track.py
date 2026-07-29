from video_analyzer import core, kernel


class _ScriptedTracker(kernel.Tracker[int]):
    """Returns one canned result per `update` call, recording everything."""

    def __init__(self, results: list[list[kernel.TrackedFace[int]]]) -> None:
        self._results = iter(results)
        self.update_calls: list[list[kernel.Face[int]]] = []
        self.reset_count = 0

    async def update(
        self, faces: list[kernel.Face[int]]
    ) -> list[kernel.TrackedFace[int]]:
        self.update_calls.append(faces)
        return next(self._results)

    async def reset(self) -> None:
        self.reset_count += 1


def _face(x: float) -> kernel.Face[int]:
    return kernel.Face(
        detection=kernel.Detection(
            bounding_box=kernel.BoundingBox(x=x, y=0.0, width=10.0, height=10.0),
            landmarks=kernel.FaceLandmarks(
                left_eye=(0.0, 0.0),
                right_eye=(0.0, 0.0),
                nose=(0.0, 0.0),
                mouth_left=(0.0, 0.0),
                mouth_right=(0.0, 0.0),
            ),
            confidence=1.0,
        ),
        embedding=0,
    )


def _tracked_face(track_id: int) -> kernel.TrackedFace[int]:
    return kernel.TrackedFace(track_id=track_id, face=_face(0.0))


async def test_no_stop_while_updates_come_back_empty() -> None:
    wrapped = _ScriptedTracker([[], []])
    stop_token = kernel.StopToken()
    tracker = core.StopOnFirstTrack(wrapped, stop_token)

    assert await tracker.update([_face(0.0)]) == []
    assert await tracker.update([]) == []

    assert not stop_token.is_stop_requested


async def test_stop_requested_on_first_confirmed_track() -> None:
    first_track = [_tracked_face(1)]
    wrapped = _ScriptedTracker([[], first_track])
    stop_token = kernel.StopToken()
    tracker = core.StopOnFirstTrack(wrapped, stop_token)

    await tracker.update([_face(0.0)])
    assert not stop_token.is_stop_requested

    assert await tracker.update([_face(1.0)]) == first_track
    assert stop_token.is_stop_requested


async def test_every_update_still_delegates_to_the_wrapped_tracker() -> None:
    wrapped = _ScriptedTracker([[], [_tracked_face(1)], [_tracked_face(1)]])
    tracker = core.StopOnFirstTrack(wrapped, kernel.StopToken())
    calls = [[_face(0.0)], [_face(1.0)], [_face(2.0)]]

    for faces in calls:
        await tracker.update(faces)

    assert wrapped.update_calls == calls


async def test_reset_delegates_to_the_wrapped_tracker() -> None:
    wrapped = _ScriptedTracker([])
    tracker = core.StopOnFirstTrack(wrapped, kernel.StopToken())

    await tracker.reset()

    assert wrapped.reset_count == 1

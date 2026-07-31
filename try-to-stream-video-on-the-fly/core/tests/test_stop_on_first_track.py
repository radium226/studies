from video_analyzer import core, kernel


class _RecordingBroadcaster(kernel.FrameBroadcaster[int, kernel.TrackedFace[int]]):
    def __init__(self) -> None:
        self.broadcast_frames: list[
            kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]
        ] = []

    async def broadcast_frame(
        self, annotated_frame: kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]
    ) -> None:
        self.broadcast_frames.append(annotated_frame)


def _tracked_face(track_id: str) -> kernel.TrackedFace[int]:
    return kernel.TrackedFace(
        track_id=track_id,
        face=kernel.Face(
            detection=kernel.Detection(
                bounding_box=kernel.BoundingBox(x=0.0, y=0.0, width=10.0, height=10.0),
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
        ),
    )


def _annotated_frame(
    frame_index: int, track_ids: list[str], *, is_scene_start: bool = False
) -> kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]:
    return kernel.AnnotatedFrame(
        frame=kernel.Frame(
            index=frame_index, content=0, is_scene_start=is_scene_start
        ),
        faces=[_tracked_face(track_id) for track_id in track_ids],
        interpolation_bracket=None,
        is_exact=False,
    )


async def test_no_stop_while_frames_carry_no_track() -> None:
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(_RecordingBroadcaster(), stop_token)

    await broadcaster.broadcast_frame(_annotated_frame(0, []))
    await broadcaster.broadcast_frame(_annotated_frame(1, []))

    assert not stop_token.is_stop_requested


async def test_default_stops_on_the_first_frame_with_a_track() -> None:
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(_RecordingBroadcaster(), stop_token)

    await broadcaster.broadcast_frame(_annotated_frame(0, []))
    assert not stop_token.is_stop_requested

    await broadcaster.broadcast_frame(_annotated_frame(1, ["1"]))
    assert stop_token.is_stop_requested


async def test_min_track_frames_counts_rendered_frames_of_one_track() -> None:
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(
        _RecordingBroadcaster(), stop_token, config=core.StopOnFirstTrackConfig(min_track_frames=3)
    )

    await broadcaster.broadcast_frame(_annotated_frame(0, ["1"]))
    await broadcaster.broadcast_frame(_annotated_frame(1, ["1"]))
    assert not stop_token.is_stop_requested

    await broadcaster.broadcast_frame(_annotated_frame(2, ["1"]))
    assert stop_token.is_stop_requested


async def test_counts_do_not_pool_across_track_ids() -> None:
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(
        _RecordingBroadcaster(), stop_token, config=core.StopOnFirstTrackConfig(min_track_frames=2)
    )

    await broadcaster.broadcast_frame(_annotated_frame(0, ["1"]))
    await broadcaster.broadcast_frame(_annotated_frame(1, ["2"]))

    assert not stop_token.is_stop_requested


async def test_a_scene_cut_resets_the_counts() -> None:
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(
        _RecordingBroadcaster(), stop_token, config=core.StopOnFirstTrackConfig(min_track_frames=2)
    )

    await broadcaster.broadcast_frame(_annotated_frame(0, ["1"]))
    # Same literal id reused across this fake's frames, standing in for two
    # different physical tracks either side of the cut — the count starts over.
    await broadcaster.broadcast_frame(_annotated_frame(1, ["1"], is_scene_start=True))
    assert not stop_token.is_stop_requested

    await broadcaster.broadcast_frame(_annotated_frame(2, ["1"]))
    assert stop_token.is_stop_requested


async def test_every_frame_still_reaches_the_wrapped_broadcaster() -> None:
    wrapped = _RecordingBroadcaster()
    stop_token = kernel.StopToken()
    broadcaster = core.StopOnFirstTrack(
        wrapped, stop_token, config=core.StopOnFirstTrackConfig(min_track_frames=1)
    )

    frames = [_annotated_frame(0, ["1"]), _annotated_frame(1, ["1"])]
    for frame in frames:
        await broadcaster.broadcast_frame(frame)

    assert wrapped.broadcast_frames == frames

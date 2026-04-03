from video_analysis import Frame, Frames


async def test_iter_frames(sample_video: Frames) -> None:
    frames: list[Frame] = []
    async for frame in sample_video._iter_frames():
        frames.append(frame)

    assert len(frames) == 10
    assert frames[0].index == 0
    assert frames[0].timestamp >= 0.0

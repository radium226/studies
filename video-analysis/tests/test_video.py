import asyncio
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from video_analysis import Detection, Frame, Rectangle, Scene, Video


@pytest_asyncio.fixture
async def sample_video(tmp_path: Path) -> Video:
    file_path = tmp_path / "sample.mp4"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f", "lavfi",
        "-i", "testsrc=duration=1:size=320x240:rate=10",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-v", "quiet",
        str(file_path),
    )
    await process.wait()
    return await Video.from_file(file_path)


@pytest_asyncio.fixture
async def multi_scene_video(tmp_path: Path) -> Video:
    file_path = tmp_path / "multi_scene.mp4"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=10",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[out]",
        "-map", "[out]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-v", "quiet",
        str(file_path),
    )
    await process.wait()
    return await Video.from_file(file_path)


@pytest_asyncio.fixture
async def video_with_dots(tmp_path: Path) -> Video:
    width, height, fps, n_frames = 320, 240, 10, 20
    file_path = tmp_path / "dots.mp4"

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-v", "quiet",
        str(file_path),
        stdin=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None

    y_grid, x_grid = np.ogrid[:height, :width]

    for i in range(n_frames):
        data = np.full((height, width, 3), 255, dtype=np.uint8)

        # Red dot (5px radius) — fixed position
        red_mask = (x_grid - 50) ** 2 + (y_grid - 120) ** 2 <= 5 ** 2
        data[red_mask] = [0, 0, 255]  # BGR

        # Blue dot (10px radius) — moves right, exits frame after frame 8
        bx = 50 + i * 30
        if bx < width:
            blue_mask = (x_grid - bx) ** 2 + (y_grid - 120) ** 2 <= 10 ** 2
            data[blue_mask] = [255, 0, 0]  # BGR

        process.stdin.write(data.tobytes())

    process.stdin.close()
    await process.wait()

    return await Video.from_file(file_path)


@pytest.mark.asyncio
async def test_iter_frames(sample_video: Video) -> None:
    frames: list[Frame] = []

    async for frame in sample_video.iter_frames():
        frames.append(frame)

    assert len(frames) == 10


@pytest.mark.asyncio
async def test_iter_scenes(multi_scene_video: Video) -> None:
    scenes: list[Scene] = []

    async for scene in multi_scene_video.iter_scenes():
        scenes.append(scene)

    assert len(scenes) == 2
    assert scenes[0].index == 0
    assert scenes[1].index == 1


@pytest.mark.asyncio
async def test_show(sample_video: Video) -> None:
    await sample_video.show()


@pytest.mark.asyncio
async def test_scene_iter_frames(multi_scene_video: Video) -> None:
    scenes: list[Scene] = []
    async for scene in multi_scene_video.iter_scenes():
        scenes.append(scene)

    frames: list[Frame] = []
    async for frame in scenes[0].iter_frames():
        frames.append(frame)

    assert len(frames) > 0
    assert frames[0].index == 0


@pytest.mark.asyncio
async def test_iter_streaks(video_with_dots: Video) -> None:
    scenes: list[Scene] = []
    async for scene in video_with_dots.iter_scenes():
        scenes.append(scene)
    assert len(scenes) == 1

    def detect_blue(frame: Frame) -> Detection[None] | None:
        b, g, r = frame.data[:, :, 0], frame.data[:, :, 1], frame.data[:, :, 2]
        mask = (b > 150) & (g < 100) & (r < 100)
        if not mask.any():
            return None
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        x, y = int(cols.min()), int(rows.min())
        w = int(cols.max() - x + 1)
        h = int(rows.max() - y + 1)
        return Detection(meta_data=None, focus_area=Rectangle(x=x, y=y, width=w, height=h))

    streaks = []
    async for streak in scenes[0].iter_streaks(detect_blue):
        streaks.append(streak)

    assert len(streaks) == 1
    assert len(streaks[0].detections) == 9  # frames 0–8: blue dot visible

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from video_analysis import Frame, Video


@pytest_asyncio.fixture
async def sample_video(tmp_path: Path) -> Path:
    path = tmp_path / "sample.mp4"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f", "lavfi",
        "-i", "testsrc=duration=1:size=320x240:rate=10",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-v", "quiet",
        str(path),
    )
    await process.wait()
    return path


@pytest.mark.asyncio
async def test_analyze_frames(sample_video: Path) -> None:
    video = await Video.from_file(sample_video)
    frame_count = 0

    async def count_frames(frame: Frame) -> None:
        nonlocal frame_count
        frame_count += 1

    await video.analyze_frames(count_frames)

    assert frame_count == 10

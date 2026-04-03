import asyncio
from pathlib import Path

import pytest_asyncio

from video_analysis import Frames, from_file


@pytest_asyncio.fixture
async def sample_video(tmp_path: Path) -> Frames:
    """1-second test video at 10fps, 320x240."""
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
    return await from_file(file_path)


@pytest_asyncio.fixture
async def multi_scene_video(tmp_path: Path) -> Frames:
    """2-second video with 2 distinct scenes (test pattern then solid blue)."""
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
    return await from_file(file_path)

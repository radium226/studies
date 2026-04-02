import asyncio
import json
from pathlib import Path

import numpy as np

from video_analysis.types import Frame, AnalyzeFrame


class Video:
    def __init__(self, path: Path, width: int, height: int, fps: float) -> None:
        self._path = path
        self._width = width
        self._height = height
        self._fps = fps

    @classmethod
    async def from_file(cls, path: Path) -> "Video":
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        stream = json.loads(stdout)["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        r_num, r_den = map(int, stream["r_frame_rate"].split("/"))
        fps = r_num / r_den
        return cls(path, width, height, fps)

    @classmethod
    async def from_url(cls, url: str) -> "Video":
        raise NotImplementedError

    async def analyze_frames(self, func: AnalyzeFrame) -> None:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(self._path),
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-v", "quiet",
            "-",
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None

        frame_size = self._width * self._height * 3
        index = 0

        try:
            while True:
                raw = await process.stdout.readexactly(frame_size)
                data = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (self._height, self._width, 3)
                )
                frame = Frame(
                    index=index,
                    timestamp=index / self._fps,
                    data=data,
                )
                await func(frame)
                index += 1
        except asyncio.IncompleteReadError:
            pass

        await process.wait()

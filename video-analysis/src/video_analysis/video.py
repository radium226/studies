import asyncio
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np

from video_analysis.types import DetectStreak, Frame, Streak


class Scene:
    def __init__(
        self,
        index: int,
        file_path: Path,
        width: int,
        height: int,
        fps: float,
        start: float,
        end: float | None,
    ) -> None:
        self.index = index
        self._file_path = file_path
        self._width = width
        self._height = height
        self._fps = fps
        self._start = start
        self._end = end

    async def iter_frames(self) -> AsyncGenerator[Frame, None]:
        args = ["ffmpeg", "-ss", str(self._start), "-i", str(self._file_path)]
        if self._end is not None:
            args += ["-t", str(self._end - self._start)]
        args += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-v", "quiet", "-"]

        process = await asyncio.create_subprocess_exec(
            *args,
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
                yield Frame(
                    index=index,
                    timestamp=self._start + index / self._fps,
                    data=data,
                )
                index += 1
        except asyncio.IncompleteReadError:
            pass

        await process.wait()

    async def iter_streaks[T](self, detect: DetectStreak[T]) -> AsyncGenerator[Streak[T], None]:
        current: list = []
        async for frame in self.iter_frames():
            detection = detect(frame)
            if detection is not None:
                detection.frame = frame
                current.append(detection)
            elif current:
                yield Streak(detections=current)
                current = []
        if current:
            yield Streak(detections=current)


class Video:
    def __init__(self, file_path: Path | None, width: int, height: int, fps: float) -> None:
        self._file_path = file_path
        self._width = width
        self._height = height
        self._fps = fps
        self._generator: AsyncGenerator[Frame, None] | None = None

    @classmethod
    async def from_file(cls, file_path: Path) -> "Video":
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        stream = json.loads(stdout)["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        r_num, r_den = map(int, stream["r_frame_rate"].split("/"))
        fps = r_num / r_den
        return cls(file_path, width, height, fps)

    @classmethod
    async def from_frames(cls, frames: AsyncGenerator[Frame, None]) -> "Video":
        first = await anext(frames)
        second = await anext(frames)
        height, width = first.data.shape[:2]
        fps = 1.0 / (second.timestamp - first.timestamp)

        async def _chained() -> AsyncGenerator[Frame, None]:
            yield first
            yield second
            async for frame in frames:
                yield frame

        video = cls(None, width, height, fps)
        video._generator = _chained()
        return video

    @classmethod
    async def from_url(cls, url: str) -> "Video":
        raise NotImplementedError

    async def iter_frames(self) -> AsyncGenerator[Frame, None]:
        if self._generator is not None:
            async for frame in self._generator:
                yield frame
            return

        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(self._file_path),
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
                yield Frame(
                    index=index,
                    timestamp=index / self._fps,
                    data=data,
                )
                index += 1
        except asyncio.IncompleteReadError:
            pass

        await process.wait()

    async def show(self) -> None:
        process = await asyncio.create_subprocess_exec(
            "ffplay",
            "-f", "rawvideo",
            "-pixel_format", "bgr24",
            "-video_size", f"{self._width}x{self._height}",
            "-framerate", str(self._fps),
            "-autoexit",
            "-i", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdin is not None
        async for frame in self.iter_frames():
            process.stdin.write(frame.data.tobytes())
        process.stdin.close()
        await process.wait()

    async def iter_scenes(self, threshold: float = 10.0) -> AsyncGenerator[Scene, None]:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(self._file_path),
            "-vf", f"scdet=threshold={threshold}",
            "-f", "null",
            "-v", "info",
            "-",
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        cuts: list[float] = []
        for line in stderr.decode().splitlines():
            m = re.search(r"lavfi\.scd\.time:\s*([\d.]+)", line)
            if m:
                cuts.append(float(m.group(1)))

        starts = [0.0, *cuts]
        ends: list[float | None] = [*cuts, None]
        for index, (start, end) in enumerate(zip(starts, ends)):
            yield Scene(index, self._file_path, self._width, self._height, self._fps, start, end)

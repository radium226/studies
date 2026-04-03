from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_analysis.types import BoundingBox, Detect, Frame


class Frames:
    def __init__(
        self,
        *,
        path: Path | None = None,
        width: int,
        height: int,
        fps: float,
        start: float = 0.0,
        end: float | None = None,
    ) -> None:
        self._path = path
        self._width = width
        self._height = height
        self._fps = fps
        self._start = start
        self._end = end

    def slice(self, start: float, end: float) -> Frames:
        absolute_start = self._start + start
        absolute_end = self._start + end
        if self._end is not None:
            absolute_end = min(absolute_end, self._end)
        return Frames(
            path=self._path,
            width=self._width,
            height=self._height,
            fps=self._fps,
            start=absolute_start,
            end=absolute_end,
        )

    async def play(self, *, crop: BoundingBox | None = None) -> None:
        args = ["ffplay"]
        args += ["-ss", str(self._start)]
        if self._end is not None:
            args += ["-t", str(self._end - self._start)]
        vf_filters: list[str] = []
        if crop:
            vf_filters.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")
        if vf_filters:
            args += ["-vf", ",".join(vf_filters)]
        args += ["-autoexit", str(self._path)]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

    async def write(self, path: Path, *, crop: BoundingBox | None = None) -> None:
        args = ["ffmpeg", "-y"]
        args += ["-ss", str(self._start)]
        args += ["-i", str(self._path)]
        if self._end is not None:
            args += ["-t", str(self._end - self._start)]
        vf_filters: list[str] = []
        if crop:
            vf_filters.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")
        if vf_filters:
            args += ["-vf", ",".join(vf_filters)]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-v", "quiet", str(path)]
        process = await asyncio.create_subprocess_exec(*args)
        await process.wait()

    async def _iter_frames(self) -> AsyncGenerator[Frame, None]:
        assert self._path is not None
        args = ["ffmpeg", "-ss", str(self._start), "-i", str(self._path)]
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


async def from_file(path: Path) -> Frames:
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
    return Frames(path=path, width=width, height=height, fps=fps)


async def detect[T](
    source: Frames, *, use: Detect[T]
) -> AsyncGenerator[Detection[T], None]:
    async for metadata, start_ts, end_ts in use(source._iter_frames()):
        yield Detection(metadata=metadata, frames=source.slice(start_ts, end_ts))


async def drain(source: Frames) -> None:
    async for _ in source._iter_frames():
        pass


@dataclass
class Detection[T]:
    metadata: T
    frames: Frames

    async def play(self) -> None:
        bbox = getattr(self.metadata, "bbox", None)
        await self.frames.play(crop=bbox)

    async def write(self, path: Path) -> None:
        bbox = getattr(self.metadata, "bbox", None)
        await self.frames.write(path, crop=bbox)

"""Video source abstraction: InputVideoLoader ABC with synthetic and URL strategies."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from video_streamer.reader import Reader, probe_video_info
from video_streamer.sample_asset import ensure_sample_asset


def _resolve_resize(
    src_w: int, src_h: int, resize: tuple[int, int]
) -> tuple[int, int]:
    """Resolve ffmpeg-style -1 placeholders to actual pixel counts.

    -1 means "keep aspect ratio, round to nearest even number".
    """
    rw, rh = resize
    if rw == -1 and rh == -1:
        return src_w, src_h
    if rw == -1:
        rw = max(1, round(src_w * rh / src_h))
        rw += rw % 2
    if rh == -1:
        rh = max(1, round(src_h * rw / src_w))
        rh += rh % 2
    return rw, rh


class InputVideoLoader(ABC):
    @property
    @abstractmethod
    def video_info(self) -> tuple[int, int, float]:
        """(width, height, fps) — valid inside the start() context."""

    @abstractmethod
    def frames(self) -> AsyncIterator[NDArray[np.uint8]]:
        """Async generator of BGR24 (H, W, 3) uint8 frames."""


class SyntheticInputVideoLoader(InputVideoLoader):
    def __init__(self, *, reader: Reader, width: int, height: int, fps: float) -> None:
        self._reader = reader
        self._width = width
        self._height = height
        self._fps = fps

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        path: Path,
        *,
        loop: bool = True,
        resize: tuple[int, int] | None = None,
        speed_factor: float = 1.0,
    ) -> AsyncIterator[SyntheticInputVideoLoader]:
        await ensure_sample_asset(path)
        w, h, fps = await probe_video_info(path)
        out_w, out_h = _resolve_resize(w, h, resize) if resize else (w, h)
        async with Reader.start(
            str(path), loop=loop, resize=resize, read_rate=speed_factor
        ) as reader:
            yield cls(reader=reader, width=out_w, height=out_h, fps=fps)

    @property
    def video_info(self) -> tuple[int, int, float]:
        return self._width, self._height, self._fps

    async def frames(self) -> AsyncIterator[NDArray[np.uint8]]:  # type: ignore[override]
        frame_size = self._width * self._height * 3
        while True:
            raw = await self._reader.read_frame(frame_size)
            if raw is None:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._height, self._width, 3)
            )


class UrlInputVideoLoader(InputVideoLoader):
    def __init__(self, *, reader: Reader, width: int, height: int, fps: float) -> None:
        self._reader = reader
        self._width = width
        self._height = height
        self._fps = fps

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        url: str,
        *,
        loop: bool = False,
        resize: tuple[int, int] | None = None,
        speed_factor: float = 1.0,
    ) -> AsyncIterator[UrlInputVideoLoader]:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--format",
            "bestvideo[ext=mp4]/bestvideo/best",
            "--get-url",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"yt-dlp failed ({proc.returncode}): {stderr.decode(errors='replace')}"
            )
        direct = stdout.decode().strip()
        w, h, fps = await probe_video_info(direct)
        out_w, out_h = _resolve_resize(w, h, resize) if resize else (w, h)
        async with Reader.start(
            direct, loop=loop, resize=resize, read_rate=speed_factor
        ) as reader:
            yield cls(reader=reader, width=out_w, height=out_h, fps=fps)

    @property
    def video_info(self) -> tuple[int, int, float]:
        return self._width, self._height, self._fps

    async def frames(self) -> AsyncIterator[NDArray[np.uint8]]:  # type: ignore[override]
        frame_size = self._width * self._height * 3
        while True:
            raw = await self._reader.read_frame(frame_size)
            if raw is None:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._height, self._width, 3)
            )

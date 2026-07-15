"""Video source abstraction: InputVideoLoader ABC with synthetic and URL strategies."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from video_streamer.reader import Reader, VideoInfo, probe_video_info
from video_streamer.sample_asset import ensure_sample_asset


def _resolve_resize(
    src_w: int, src_h: int, resize: tuple[int, int]
) -> tuple[int, int]:
    """Resolve ffmpeg-style -1 placeholders to actual pixel counts.

    -1 means "keep aspect ratio, round to nearest even number". Always clamps
    the result to even dimensions (even the (-1, -1) "keep native" passthrough)
    since libx264's yuv420p output requires even width/height — an odd-sized
    source would otherwise make the encoder exit immediately on startup.
    """
    rw, rh = resize
    if rw == -1 and rh == -1:
        rw, rh = src_w, src_h
    elif rw == -1:
        rw = max(1, round(src_w * rh / src_h))
        rw += rw % 2
    elif rh == -1:
        rh = max(1, round(src_h * rw / src_w))
        rh += rh % 2
    rw -= rw % 2
    rh -= rh % 2
    return rw, rh


@asynccontextmanager
async def _open_reader(
    source: str | Path,
    *,
    loop: bool,
    resize: tuple[int, int] | None,
    speed_factor: float,
) -> AsyncIterator[tuple[Reader, VideoInfo]]:
    """Common tail of every loader start(): probe the source, resolve the
    post-resize output size, and spawn the decoding Reader."""
    w, h, fps = await probe_video_info(source)
    out_w, out_h = _resolve_resize(w, h, resize or (-1, -1))
    decoder_resize = (out_w, out_h) if (out_w, out_h) != (w, h) else None
    async with Reader.start(
        str(source), loop=loop, resize=decoder_resize, read_rate=speed_factor
    ) as reader:
        yield reader, VideoInfo(out_w, out_h, fps)


class InputVideoLoader(ABC):
    @property
    @abstractmethod
    def video_info(self) -> VideoInfo:
        """(width, height, fps) — valid inside the start() context."""

    @abstractmethod
    def frames(self) -> AsyncIterator[NDArray[np.uint8]]:
        """Async generator of BGR24 (H, W, 3) uint8 frames."""


class ReaderInputVideoLoader(InputVideoLoader):
    """Concrete base for loaders that decode through a Reader subprocess.

    Subclasses differ only in how they resolve their source (generating the
    sample asset, resolving a page URL, ...) before handing it to
    `_open_reader`; frame delivery is identical for all of them.
    """

    def __init__(self, *, reader: Reader, video_info: VideoInfo) -> None:
        self._reader = reader
        self._video_info = video_info

    @property
    def video_info(self) -> VideoInfo:
        return self._video_info

    async def frames(self) -> AsyncIterator[NDArray[np.uint8]]:  # type: ignore[override]
        width, height, _ = self._video_info
        frame_size = width * height * 3
        while True:
            raw = await self._reader.read_frame(frame_size)
            if raw is None:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))


class SyntheticInputVideoLoader(ReaderInputVideoLoader):
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
        async with _open_reader(
            path, loop=loop, resize=resize, speed_factor=speed_factor
        ) as (reader, video_info):
            yield cls(reader=reader, video_info=video_info)


class UrlInputVideoLoader(ReaderInputVideoLoader):
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
        direct = await _resolve_direct_media_url(url)
        async with _open_reader(
            direct, loop=loop, resize=resize, speed_factor=speed_factor
        ) as (reader, video_info):
            yield cls(reader=reader, video_info=video_info)


async def _resolve_direct_media_url(url: str) -> str:
    """Resolve a page URL to a direct media URL via yt-dlp."""
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
    return stdout.decode().strip()

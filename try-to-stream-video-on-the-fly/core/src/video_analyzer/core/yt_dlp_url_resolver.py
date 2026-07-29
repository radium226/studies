"""Resolve a page URL (YouTube, etc.) to a direct media URL via the `yt-dlp` CLI,
so it can be handed to `FfmpegFrameSource` like any other source string."""

from __future__ import annotations

import asyncio

DEFAULT_FORMAT = "bestvideo[ext=mp4]/bestvideo/best"


async def resolve_direct_media_url(url: str, *, format: str = DEFAULT_FORMAT) -> str:
    """Run `yt-dlp --get-url` on a page URL and return the direct media URL.

    Requires the `yt-dlp` binary on PATH (a subprocess, not a Python package —
    same convention as ffmpeg/ffprobe). Raises RuntimeError with yt-dlp's
    stderr if resolution fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--format",
        format,
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

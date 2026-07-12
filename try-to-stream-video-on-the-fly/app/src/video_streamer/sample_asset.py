"""Generate a self-contained synthetic sample video so the project needs no external media."""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_sample_asset(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=25",
            "-t",
            "30",
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
    )

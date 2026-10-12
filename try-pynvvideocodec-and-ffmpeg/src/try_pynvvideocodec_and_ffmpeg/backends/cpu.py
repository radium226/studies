"""No GPU needed: ffmpeg decodes, PyTorch (CPU) processes, ffmpeg encodes with libx265.

Frames go through pipes as raw RGB, back to back.
"""

import json
import subprocess
from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from fractions import Fraction
from io import BufferedReader
from pathlib import Path
from typing import cast

import torch

from ..core import FrameSink, FrameSource, VideoInfo


def probe(path: Path) -> VideoInfo:
    # fmt: off
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,codec_name",
            "-of", "json",
            str(path),
        ],
        capture_output=True, check=True, text=True,
    )
    # fmt: on
    stream = json.loads(result.stdout)["streams"][0]
    return VideoInfo(
        width=stream["width"],
        height=stream["height"],
        fps=Fraction(stream["avg_frame_rate"]),
        codec=stream["codec_name"],
    )


class FfmpegSource:
    def __init__(self, path: Path):
        self.info = probe(path)
        # fmt: off
        self.decoder = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", str(path),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ],
            stdout=subprocess.PIPE,
        )
        # fmt: on

    def __iter__(self) -> Iterator[torch.Tensor]:
        stdout = cast(BufferedReader, self.decoder.stdout)
        # One buffer, reused for every frame: the tensors we yield alias it
        buffer = bytearray(self.info.width * self.info.height * 3)
        frame = torch.frombuffer(buffer, dtype=torch.uint8).view(self.info.height, self.info.width, 3)
        while stdout.readinto(buffer) == len(buffer):
            yield frame

    def close(self) -> None:
        if self.decoder.poll() is None:
            self.decoder.terminate()
        if self.decoder.wait() not in (0, -15):  # -15: SIGTERM, if we stopped early
            raise RuntimeError(f"ffmpeg (decoder) exited with code {self.decoder.returncode}")


class FfmpegSink:
    def __init__(self, path: Path, info: VideoInfo):
        # ffmpeg encodes and muxes itself, so it knows the real timestamps:
        # unlike on the GPU, B-frames are fine here
        # fmt: off
        self.encoder = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{info.width}x{info.height}",
                "-framerate", f"{info.fps.numerator}/{info.fps.denominator}", "-i", "pipe:0",
                "-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
                "-x265-params", "log-level=error",
                str(path),
            ],
            stdin=subprocess.PIPE,
        )
        # fmt: on

    def write(self, frame: torch.Tensor) -> None:
        assert self.encoder.stdin is not None
        self.encoder.stdin.write(frame.contiguous().numpy().data)

    def close(self) -> None:
        assert self.encoder.stdin is not None
        self.encoder.stdin.close()
        if self.encoder.wait() != 0:
            raise RuntimeError(f"ffmpeg (encoder) exited with code {self.encoder.returncode}")


class CPUBackend:
    def __init__(self):
        self.device = torch.device("cpu")

    @staticmethod
    @contextmanager
    def create() -> Generator["CPUBackend"]:
        # Nothing to set up: ffmpeg is started per source / sink
        yield CPUBackend()

    @contextmanager
    def open_source(self, path: Path) -> Generator[FrameSource]:
        with closing(FfmpegSource(path)) as source:
            yield source

    @contextmanager
    def open_sink(self, path: Path, info: VideoInfo) -> Generator[FrameSink]:
        with closing(FfmpegSink(path, info)) as sink:
            yield sink

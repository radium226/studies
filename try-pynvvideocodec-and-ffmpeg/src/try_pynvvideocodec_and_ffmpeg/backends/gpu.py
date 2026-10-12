"""Everything stays on the GPU: NVDEC → PyTorch (CUDA) → NVENC.

Only the compressed bitstream comes back to the host, where it is piped into
`ffmpeg -c copy` to get an MP4.
"""

import subprocess
from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from fractions import Fraction
from pathlib import Path

import PyNvVideoCodec as nvc
import torch

from ..core import FrameSink, FrameSource, VideoInfo


class CudaArray:
    """Expose a tensor only through `__cuda_array_interface__`.

    The encoder probes `__dlpack__` first and calls it with a positional `stream`
    argument, which `torch.Tensor.__dlpack__` refuses, so we hide it.
    """

    def __init__(self, tensor: torch.Tensor):
        self.tensor = tensor  # Keep the memory alive while NVENC reads it
        self.__cuda_array_interface__ = tensor.__cuda_array_interface__


class RGBAFrame:
    """What `PyNvEncoder.Encode()` expects for ABGR: `.cuda()` returning ONE CAI object (not a list)."""

    def __init__(self, rgba: torch.Tensor):
        self.rgba = CudaArray(rgba)

    def cuda(self) -> CudaArray:
        return self.rgba


class NvDecSource:
    def __init__(self, path: Path):
        # PyNvVideoCodec's members come from a compiled extension, invisible to type checkers
        rgb = nvc.OutputColorType.RGB  # type: ignore
        self.decoder = nvc.SimpleDecoder(str(path), use_device_memory=True, output_color_type=rgb)
        metadata = self.decoder.get_stream_metadata()
        self.info = VideoInfo(
            width=metadata.width,
            height=metadata.height,
            fps=Fraction(metadata.average_fps).limit_denominator(1001),
            codec=metadata.codec_name,
        )

    def __iter__(self) -> Iterator[torch.Tensor]:
        for frame in self.decoder:
            # Zero-copy view on the decoder surface: (H, W, 3) uint8, RGB
            yield torch.from_dlpack(frame)

    def close(self) -> None:
        pass


class NvEncSink:
    def __init__(self, path: Path, info: VideoInfo):
        fps = f"{info.fps.numerator}/{info.fps.denominator}"
        # No B-frames: the raw stream we hand to ffmpeg has no timestamps, so
        # ffmpeg assumes decode order == display order, which B-frames would
        # break. Byte order in memory is R, G, B, A despite the "ABGR" name
        # (NVENC names it as a little-endian 32-bit word).
        self.encoder = nvc.CreateEncoder(
            info.width, info.height, "ABGR", False, codec="hevc", preset="P4", fps=fps, bf=0
        )
        self.alpha = torch.full((info.height, info.width, 1), 255, dtype=torch.uint8, device="cuda")

        # NVENC outputs an Annex-B HEVC elementary stream: let ffmpeg wrap it
        # into MP4 without re-encoding. Raw streams carry no timestamps, hence
        # `-framerate`.
        # fmt: off
        self.muxer = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "hevc", "-framerate", fps, "-i", "pipe:0",
                "-c:v", "copy", "-tag:v", "hvc1",
                str(path),
            ],
            stdin=subprocess.PIPE,
        )
        # fmt: on

    def write(self, frame: torch.Tensor) -> None:
        rgba = torch.cat([frame, self.alpha], dim=2)
        # PyTorch kernels are asynchronous and NVENC copies its input on its own
        # stream: wait, or it reads `rgba` before it is written. This also
        # guarantees the source's surface has been read before it is recycled.
        torch.cuda.current_stream().synchronize()
        self._mux(self.encoder.Encode(RGBAFrame(rgba)))

    def close(self) -> None:
        self._mux(self.encoder.EndEncode())
        assert self.muxer.stdin is not None
        self.muxer.stdin.close()
        if self.muxer.wait() != 0:
            raise RuntimeError(f"ffmpeg (muxer) exited with code {self.muxer.returncode}")

    def _mux(self, bitstreams: list[dict]) -> None:
        assert self.muxer.stdin is not None
        for bitstream in bitstreams:
            self.muxer.stdin.write(bitstream["data"])


class GPUBackend:
    def __init__(self, device: torch.device):
        self.device = device

    @staticmethod
    @contextmanager
    def create() -> Generator["GPUBackend"]:
        # Fails early, with a clear error, when there is no GPU
        torch.cuda.init()
        yield GPUBackend(torch.device("cuda"))

    @contextmanager
    def open_source(self, path: Path) -> Generator[FrameSource]:
        with closing(NvDecSource(path)) as source:
            yield source

    @contextmanager
    def open_sink(self, path: Path, info: VideoInfo) -> Generator[FrameSink]:
        with closing(NvEncSink(path, info)) as sink:
            yield sink

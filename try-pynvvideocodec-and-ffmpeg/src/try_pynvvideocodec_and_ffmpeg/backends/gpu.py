"""Everything stays on the GPU: NVDEC → PyTorch (CUDA) → NVENC.

Only the compressed bitstream comes back to the host, where it is piped into
`ffmpeg -c copy` to get an MP4.
"""

import subprocess
from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Literal

import PyNvVideoCodec as nvc
import structlog
import torch

from ..core import FrameSink, FrameSource, VideoInfo

log = structlog.get_logger()


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
        log.debug("nvdec.decoder", info=self.info, output_color="RGB", memory="device")

    def __iter__(self) -> Iterator[torch.Tensor]:
        # `SimpleDecoder` only has `__getitem__` (no `__iter__`): `iter()` turns it into a real iterator
        for index, frame in enumerate(iter(self.decoder)):
            # Zero-copy view on the decoder surface: (H, W, 3) uint8, RGB
            tensor = torch.from_dlpack(frame)
            if index == 0:
                # Same address as the decoder's surface: nothing was copied
                log.debug(
                    "nvdec.frame", shape=tuple(tensor.shape), device=str(tensor.device), data_ptr=hex(tensor.data_ptr())
                )
            yield tensor

    def close(self) -> None:
        pass


def rgb_to_yuv_standard(width: int, height: int) -> Literal["bt601", "bt709"]:
    """The matrix PyNvVideoCodec uses to convert RGB input to YUV before NVENC.

    It's not configurable nor documented: measured by encoding solid colors and
    reading back the raw YUV. Up to 768x576 (SD), BT.601; above, BT.709.
    """
    return "bt601" if width <= 768 and height <= 576 else "bt709"


# H.265 VUI codes (colour_primaries, transfer_characteristics, matrix_coefficients)
VUI_CODES = {"bt601": 6, "bt709": 1}


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
        # One RGBA buffer for the whole video, alpha set once: each frame only
        # fills the RGB channels (no `torch.cat`, no allocation per frame)
        self.rgba = torch.full((info.height, info.width, 4), 255, dtype=torch.uint8, device="cuda")
        standard = rgb_to_yuv_standard(info.width, info.height)
        log.debug(
            "nvenc.encoder", input_format="ABGR", codec="hevc", preset="P4", b_frames=0, fps=fps, rgb_to_yuv=standard
        )
        self.frames = 0
        self.packets = 0
        self.bytes = 0

        # NVENC outputs an Annex-B HEVC elementary stream: let ffmpeg wrap it
        # into MP4 without re-encoding. Raw streams carry no timestamps, hence
        # `-framerate`. NVENC doesn't say which RGB to YUV matrix was used: the
        # `hevc_metadata` filter writes it in the stream (still no re-encoding),
        # or players would guess (ffmpeg always guesses BT.601).
        code = VUI_CODES[standard]
        vui = f"colour_primaries={code}:transfer_characteristics={code}:matrix_coefficients={code}:video_full_range_flag=0"
        # fmt: off
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "hevc", "-framerate", fps, "-i", "pipe:0",
            "-c:v", "copy", "-tag:v", "hvc1", "-bsf:v", f"hevc_metadata={vui}",
            str(path),
        ]
        # fmt: on
        log.debug("ffmpeg.muxer.started", command=" ".join(command))
        self.muxer = subprocess.Popen(command, stdin=subprocess.PIPE)

    def write(self, frame: torch.Tensor) -> None:
        rgba = self.rgba
        rgba[..., :3].copy_(frame)
        # PyTorch kernels are asynchronous and NVENC copies its input on its own
        # stream: wait, or it reads `rgba` before it is written. This also
        # guarantees the source's surface has been read before it is recycled.
        torch.cuda.current_stream().synchronize()
        if self.frames == 0:
            log.debug("nvenc.input", shape=tuple(rgba.shape), device=str(rgba.device), data_ptr=hex(rgba.data_ptr()))
        self._mux(self.encoder.Encode(RGBAFrame(rgba)))
        self.frames += 1

    def close(self) -> None:
        # NVENC buffers a few frames (lookahead): the last packets only come out now
        flushed = self.encoder.EndEncode()
        log.debug("nvenc.flushed", packets=len(flushed))
        self._mux(flushed)
        assert self.muxer.stdin is not None
        self.muxer.stdin.close()
        if self.muxer.wait() != 0:
            raise RuntimeError(f"ffmpeg (muxer) exited with code {self.muxer.returncode}")
        log.debug("ffmpeg.muxer.stopped", frames=self.frames, packets=self.packets, hevc_bytes=self.bytes)

    def _mux(self, bitstreams: list[dict]) -> None:
        assert self.muxer.stdin is not None
        for bitstream in bitstreams:
            self.muxer.stdin.write(bitstream["data"])
            self.packets += 1
            self.bytes += len(bitstream["data"])


class GPUBackend:
    def __init__(self, device: torch.device):
        self.device = device

    @staticmethod
    @contextmanager
    def create() -> Generator["GPUBackend"]:
        # Fails early, with a clear error, when there is no GPU
        torch.cuda.init()
        device = torch.device("cuda")
        log.debug("gpu.device", name=torch.cuda.get_device_name(device), cuda=torch.version.cuda)
        yield GPUBackend(device)

    @contextmanager
    def open_source(self, path: Path) -> Generator[FrameSource]:
        with closing(NvDecSource(path)) as source:
            yield source

    @contextmanager
    def open_sink(self, path: Path, info: VideoInfo) -> Generator[FrameSink]:
        with closing(NvEncSink(path, info)) as sink:
            yield sink

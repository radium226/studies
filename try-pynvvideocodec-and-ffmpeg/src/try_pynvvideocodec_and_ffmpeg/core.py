"""The pieces of a pipeline, independent of where they run.

Every frame is an `(H, W, 3)` `uint8` RGB tensor. Its `device` (CPU or GPU) is
the only thing that changes from one backend to another.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

import torch


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: Fraction
    codec: str


class FrameSource(Protocol):
    """Decodes a video into frames."""

    info: VideoInfo

    def __iter__(self) -> Iterator[torch.Tensor]:
        """Yield frames in display order.

        A frame may alias a buffer the source reuses: it is only valid until the
        next one is requested.
        """
        ...


class FrameTransformer[S](Protocol):
    """Turns a frame into a new one, on the same device.

    `S` is the transformer's settings type. Use it as
    `with TransformerType.open(info, device, settings) as frame_transformer:`,
    then `frame_transformer.transform_frame(frame)` for each frame: `open()` is
    where anything expensive (a model, a mask…) is loaded, once for the whole
    video, and the only way to get an instance.
    """

    @staticmethod
    def open(info: VideoInfo, device: torch.device, settings: S) -> AbstractContextManager["FrameTransformer[S]"]: ...

    def transform_frame(self, frame: torch.Tensor) -> torch.Tensor:
        """Must not modify its input. Only valid until `open()` exits."""
        ...


class BatchFrameTransformer[S](Protocol):
    """Like `FrameTransformer`, but turns a batch of frames into new ones at once.

    Frames come stacked in one `(N, H, W, 3)` `uint8` tensor, so that every
    operation runs on the N frames in parallel.
    """

    @staticmethod
    def open(
        info: VideoInfo, device: torch.device, settings: S
    ) -> AbstractContextManager["BatchFrameTransformer[S]"]: ...

    def transform_frames(self, frames: torch.Tensor) -> torch.Tensor:
        """`(N, H, W, 3)` in, `(N, H, W, 3)` out. Must not modify its input. Only valid until `open()` exits."""
        ...


class FrameSink(Protocol):
    """Encodes frames and writes them to a video file."""

    def write(self, frame: torch.Tensor) -> None: ...


class Backend(Protocol):
    """Where frames are decoded, processed and encoded.

    Passed around as a class (`type[Backend]`), and instantiated with
    `with backend_type.create() as backend:`, which sets up (and tears down)
    whatever the backend needs, once.
    """

    device: torch.device  # Where the source yields frames, and the sink expects them

    @staticmethod
    def create() -> AbstractContextManager["Backend"]: ...

    def open_source(self, path: Path) -> AbstractContextManager[FrameSource]:
        """Released on exit."""
        ...

    def open_sink(self, path: Path, info: VideoInfo) -> AbstractContextManager[FrameSink]:
        """Flushes what is left and finalizes the file on exit."""
        ...

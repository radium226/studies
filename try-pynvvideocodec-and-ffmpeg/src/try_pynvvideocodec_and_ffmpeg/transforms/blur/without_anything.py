from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

import structlog
import torch

from ...core import VideoInfo
from .model import DEFAULT_SIGMA, Model

log = structlog.get_logger()


class WithoutAnything:
    """Blur batches of frames with `Model`, in plain PyTorch."""

    @dataclass(frozen=True)
    class Settings:
        sigma: float = DEFAULT_SIGMA  # In pixels

    def __init__(self, model: Model):
        self.model = model

    @staticmethod
    @contextmanager
    def open(
        info: VideoInfo, device: torch.device, settings: "WithoutAnything.Settings"
    ) -> Generator["WithoutAnything"]:
        # The model handles any resolution: `info` is not needed here
        # float16 on the GPU is precise enough to average 8-bit values, for half
        # the memory traffic. CPUs have no fast float16 convolution
        dtype = torch.float16 if device.type == "cuda" else torch.float32
        model = Model(settings.sigma).to(device=device, dtype=dtype)
        model.eval()
        log.debug("blur.kernel", sigma=settings.sigma, size=model.kernel.numel(), dtype=str(dtype), device=str(device))
        yield WithoutAnything(model)

    def transform_frames(self, frames: torch.Tensor) -> torch.Tensor:
        with torch.inference_mode():
            return self.model(frames)

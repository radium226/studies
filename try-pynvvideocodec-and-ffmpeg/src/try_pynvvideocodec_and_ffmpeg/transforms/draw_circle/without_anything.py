from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

import structlog
import torch

from ...core import VideoInfo

DEFAULT_RADIUS_RATIO = 0.2  # Relative to the frame height

log = structlog.get_logger()


class WithoutAnything:
    """Draw a filled white circle in the middle of the frame, with plain PyTorch."""

    @dataclass(frozen=True)
    class Settings:
        radius_ratio: float = DEFAULT_RADIUS_RATIO  # Relative to the frame height

    def __init__(self, mask: torch.Tensor):
        self.mask = mask

    @staticmethod
    @contextmanager
    def open(
        info: VideoInfo, device: torch.device, settings: "WithoutAnything.Settings"
    ) -> Generator["WithoutAnything"]:
        # (H, W) bool mask, computed once for the whole video
        y = torch.arange(info.height, device=device).view(-1, 1)
        x = torch.arange(info.width, device=device).view(1, -1)
        radius = info.height * settings.radius_ratio
        mask = (x - info.width / 2) ** 2 + (y - info.height / 2) ** 2 <= radius**2
        log.debug("draw_circle.mask", radius_px=round(radius), pixels=int(mask.sum()), device=str(mask.device))
        yield WithoutAnything(mask)

    def transform_frame(self, frame: torch.Tensor) -> torch.Tensor:
        output = frame.clone()
        output[self.mask] = 255
        return output

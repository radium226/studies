"""The circle's color, stored as weights in a SafeTensors file.

Unlike ONNX, SafeTensors holds no computation, only named tensors: the code is
`Model` (shared with the ONNX export), and the file holds its `state_dict()`,
here a single `color` tensor. `export_safetensors` writes it, and
`WithSafeTensors` loads it straight onto the frames' device, into a fresh
`Model`, which then runs in plain PyTorch.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
import torch
from safetensors.torch import load_file, save_file

from ...core import VideoInfo
from .with_onnx import DEFAULT_COLOR, Color, Model
from .without_anything import DEFAULT_RADIUS_RATIO

log = structlog.get_logger()


def export_safetensors(path: Path, color: Color = DEFAULT_COLOR) -> None:
    save_file(Model(color).state_dict(), path)


class WithSafeTensors:
    """Draw the circle with `Model`, its weights loaded from `export_safetensors()`."""

    @dataclass(frozen=True)
    class Settings:
        weights_path: Path
        radius_ratio: float = DEFAULT_RADIUS_RATIO  # Relative to the frame height

    def __init__(self, model: Model, radius_ratio: torch.Tensor):
        self.model = model
        self.radius_ratio = radius_ratio  # 0-d float32 tensor, on the frames' device

    @staticmethod
    @contextmanager
    def open(
        info: VideoInfo, device: torch.device, settings: "WithSafeTensors.Settings"
    ) -> Generator["WithSafeTensors"]:
        # The model handles any resolution: `info` is not needed here
        # Loaded directly on `device` (no pickle, no CPU round trip for the GPU), then
        # `assign=True` makes the model use these tensors as they are, instead of
        # copying them into its own (CPU) buffers
        state_dict = load_file(settings.weights_path, device=str(device))
        model = Model()
        model.load_state_dict(state_dict, assign=True)
        model.eval()
        log.info(
            "safetensors.loaded",
            path=str(settings.weights_path),
            tensors={
                name: (tuple(tensor.shape), str(tensor.dtype), str(tensor.device))
                for name, tensor in state_dict.items()
            },
        )
        radius_ratio = torch.tensor(settings.radius_ratio, dtype=torch.float32, device=device)
        yield WithSafeTensors(model, radius_ratio)

    def transform_frame(self, frame: torch.Tensor) -> torch.Tensor:
        with torch.inference_mode():
            return self.model(frame, self.radius_ratio)

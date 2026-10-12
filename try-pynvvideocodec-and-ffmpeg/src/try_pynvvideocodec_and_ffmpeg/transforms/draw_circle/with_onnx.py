"""The circle, hidden in an ONNX model.

`Model` is exported once to a `.onnx` file (`export_onnx`), then `WithONNX` runs
that file with ONNX Runtime: on the GPU through IO binding (zero-copy, ONNX
Runtime reads and writes the tensors' memory directly), or on the CPU through
NumPy. The radius is an input of the model, so one file works for any radius.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
import structlog
import torch
from torch.export import Dim

from ...core import VideoInfo
from .without_anything import DEFAULT_RADIUS_RATIO

FRAME_INPUT = "frame"
RADIUS_RATIO_INPUT = "radius_ratio"
OUTPUT_NAME = "output"

type Color = tuple[int, int, int]  # RGB, 0-255

DEFAULT_COLOR: Color = (255, 255, 255)

log = structlog.get_logger()


class Model(torch.nn.Module):
    """(H, W, 3) uint8 RGB → same, with a filled circle in the middle.

    `radius_ratio` (relative to the frame height) is an input, not a constant: once
    exported to ONNX, the same model draws any radius, chosen at runtime. `color`
    is the model's state (a buffer, in its `state_dict()`): baked into the graph by
    the ONNX export, and the only tensor in the SafeTensors export.
    """

    color: torch.Tensor

    def __init__(self, color: Color = DEFAULT_COLOR):
        super().__init__()
        self.register_buffer("color", torch.tensor(color, dtype=torch.uint8))

    def forward(self, frame: torch.Tensor, radius_ratio: torch.Tensor) -> torch.Tensor:
        y = torch.arange(frame.shape[0], device=frame.device, dtype=torch.float32)
        x = torch.arange(frame.shape[1], device=frame.device, dtype=torch.float32)
        # Sizes as tensors, not Python scalars: with dynamic shapes, `frame.shape[0] / 2`
        # is a symbolic float the ONNX exporter fails to convert
        height, width = y[-1] + 1, x[-1] + 1
        mask = (x.view(1, -1) - width / 2) ** 2 + (y.view(-1, 1) - height / 2) ** 2 <= (height * radius_ratio) ** 2
        return torch.where(mask.unsqueeze(-1), self.color, frame)


def export_onnx(path: Path, color: Color = DEFAULT_COLOR) -> None:
    example_frame = torch.zeros((1080, 1920, 3), dtype=torch.uint8)
    example_radius_ratio = torch.tensor(DEFAULT_RADIUS_RATIO, dtype=torch.float32)
    torch.onnx.export(
        Model(color),  # The color ends up as a constant (an "initializer") in the graph
        (example_frame, example_radius_ratio),
        path,
        input_names=[FRAME_INPUT, RADIUS_RATIO_INPUT],
        output_names=[OUTPUT_NAME],
        # Any resolution, not only the example's; the radius is a 0-d tensor
        dynamic_shapes={"frame": {0: Dim("height"), 1: Dim("width")}, "radius_ratio": None},
        # The model is tiny: keep everything in one file, no `.onnx.data` next to it
        external_data=False,
        dynamo=True,
    )


class WithONNX:
    """Draw the circle by running a model exported with `export_onnx()`."""

    @dataclass(frozen=True)
    class Settings:
        model_path: Path
        radius_ratio: float = DEFAULT_RADIUS_RATIO  # Relative to the frame height

    def __init__(self, session: ort.InferenceSession, radius_ratio: torch.Tensor):
        self.session = session
        self.radius_ratio = radius_ratio  # 0-d float32 tensor, on the frames' device

    @staticmethod
    @contextmanager
    def open(info: VideoInfo, device: torch.device, settings: "WithONNX.Settings") -> Generator["WithONNX"]:
        # The model handles any resolution: `info` is not needed here
        if device.type == "cuda":
            # Load the CUDA libraries from the `nvidia-*` wheels PyTorch already depends on
            ort.preload_dlls()
            providers = [("CUDAExecutionProvider", {"device_id": device.index or 0})]
        else:
            providers = ["CPUExecutionProvider"]
        # ONNX Runtime warns that it runs shape-related nodes on the CPU (on a few
        # bytes, which is expected) and about plugin EPs: we log what matters instead
        ort.set_default_logger_severity(3)
        session = ort.InferenceSession(str(settings.model_path), providers=providers)
        log.info("onnx.session", model=str(settings.model_path), providers=session.get_providers())
        radius_ratio = torch.tensor(settings.radius_ratio, dtype=torch.float32, device=device)
        yield WithONNX(session, radius_ratio)

    def transform_frame(self, frame: torch.Tensor) -> torch.Tensor:
        if frame.is_cuda:
            return self._run_on_gpu(self.session, frame)
        # Synchronous, and NumPy views share the tensors' memory: no copy
        inputs = {FRAME_INPUT: frame.numpy(), RADIUS_RATIO_INPUT: self.radius_ratio.numpy()}
        return torch.from_numpy(self.session.run([OUTPUT_NAME], inputs)[0])

    def _run_on_gpu(self, session: ort.InferenceSession, frame: torch.Tensor) -> torch.Tensor:
        frame = frame.contiguous()
        output = torch.empty_like(frame)
        # Hand ONNX Runtime raw device pointers, so that nothing goes through the
        # host (without IO binding, inputs and outputs are NumPy arrays)
        device_id = frame.device.index or 0
        binding = session.io_binding()
        binding.bind_input(FRAME_INPUT, "cuda", device_id, np.uint8, tuple(frame.shape), frame.data_ptr())
        binding.bind_input(RADIUS_RATIO_INPUT, "cuda", device_id, np.float32, (), self.radius_ratio.data_ptr())
        binding.bind_output(OUTPUT_NAME, "cuda", device_id, np.uint8, tuple(output.shape), output.data_ptr())
        # ONNX Runtime runs on its own CUDA stream: make sure `frame` is ready
        torch.cuda.current_stream().synchronize()
        session.run_with_iobinding(binding)
        return output

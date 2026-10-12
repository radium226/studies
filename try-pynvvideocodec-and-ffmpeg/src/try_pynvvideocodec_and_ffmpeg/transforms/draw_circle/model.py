import torch

type Color = tuple[int, int, int]  # RGB, 0-255

DEFAULT_COLOR: Color = (255, 255, 255)


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

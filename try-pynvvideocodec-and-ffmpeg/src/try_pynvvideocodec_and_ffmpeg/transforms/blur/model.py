import math

import torch
import torch.nn.functional as F

DEFAULT_SIGMA = 4.0  # In pixels


def gaussian_kernel(sigma: float) -> torch.Tensor:
    """(2r + 1,) float32 weights of a Gaussian, summing to 1, with r = ⌈3σ⌉ (99.7% of the bell curve)."""
    radius = math.ceil(3 * sigma)
    x = torch.arange(-radius, radius + 1, dtype=torch.float32)
    kernel = torch.exp(-(x**2) / (2 * sigma**2))
    return kernel / kernel.sum()  # Sums to 1: brightness is preserved


class Model(torch.nn.Module):
    """(N, H, W, 3) uint8 RGB → same, blurred by a Gaussian of standard deviation `sigma` (in pixels).

    The 2D Gaussian is the outer product of a 1D one with itself (it's
    separable): blurring the rows, then the columns, gives the same result as
    one 2D convolution, for 2(2r + 1) multiplications per pixel instead of
    (2r + 1)². `kernel` is the model's state (a buffer, in its `state_dict()`),
    and its dtype is the one the convolutions run in.
    """

    kernel: torch.Tensor

    def __init__(self, sigma: float = DEFAULT_SIGMA):
        super().__init__()
        self.register_buffer("kernel", gaussian_kernel(sigma))

    def forward(self, frames: torch.Tensor) -> torch.Tensor:
        radius = (self.kernel.numel() - 1) // 2
        # (N, H, W, 3) → (N, 3, H, W), as `conv2d` expects (a view: only the strides change)
        x = frames.permute(0, 3, 1, 2).to(self.kernel.dtype)
        # Pixels past the borders repeat the edge ones (zeros, `conv2d`'s own padding,
        # would darken the borders)
        x = F.pad(x, (radius, radius, radius, radius), mode="replicate")
        # One (1, K) then one (K, 1) kernel per channel: with `groups=3`, each
        # channel is convolved with its own copy, and channels don't mix
        horizontal = self.kernel.view(1, 1, 1, -1).expand(3, 1, 1, -1)
        vertical = self.kernel.view(1, 1, -1, 1).expand(3, 1, -1, 1)
        x = F.conv2d(F.conv2d(x, horizontal, groups=3), vertical, groups=3)
        return x.round().clamp(0, 255).to(torch.uint8).permute(0, 2, 3, 1)

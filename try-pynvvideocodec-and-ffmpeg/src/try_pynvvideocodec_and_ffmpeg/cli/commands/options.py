from typing import Literal

import click

from ...core import Backend
from ...transforms import DEFAULT_COLOR

type BackendType = Literal["cpu", "gpu"]

color_option = click.option(
    "--color",
    type=(click.IntRange(0, 255), click.IntRange(0, 255), click.IntRange(0, 255)),
    default=DEFAULT_COLOR,
    show_default=True,
    metavar="R G B",
    help="Circle color, stored in the exported file.",
)

backend_option = click.option(
    "--backend",
    "backend_type",
    type=click.Choice(["cpu", "gpu"]),
    default="gpu",
    show_default=True,
    help="gpu: NVDEC → PyTorch (CUDA) → NVENC. cpu: ffmpeg → PyTorch (CPU) → ffmpeg (libx265), no GPU needed.",
)


def load_backend(backend_type: BackendType) -> type[Backend]:
    # Imported lazily: the CPU backend must work on machines without the NVIDIA
    # driver, where loading PyNvVideoCodec may fail
    match backend_type:
        case "cpu":
            from ...backends.cpu import CPUBackend

            return CPUBackend
        case "gpu":
            from ...backends.gpu import GPUBackend

            return GPUBackend

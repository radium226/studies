from pathlib import Path

import click

from ...pipeline import run_batched as run_batched_pipeline
from ...transforms import DEFAULT_SIGMA, BlurWithoutAnything
from .options import BackendType, backend_option, load_backend

DEFAULT_BATCH_SIZE = 8


@click.command()
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@backend_option
@click.option(
    "--sigma",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_SIGMA,
    show_default=True,
    help="Blur strength: the Gaussian's standard deviation, in pixels.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(min=1),
    default=DEFAULT_BATCH_SIZE,
    show_default=True,
    help="Frames blurred at once, stacked in one tensor.",
)
def blur(input_path: Path, output_path: Path, backend_type: BackendType, sigma: float, batch_size: int) -> None:
    """Blur INPUT_PATH with a Gaussian (conv2d, plain PyTorch), and write the result to OUTPUT_PATH."""
    settings = BlurWithoutAnything.Settings(sigma=sigma)
    run_batched_pipeline(load_backend(backend_type), BlurWithoutAnything, settings, input_path, output_path, batch_size)

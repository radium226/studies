import logging

import click
import structlog

from .commands import blur, export_onnx, export_safetensors, export_tensorrt, run


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Show debug logs.")
def main(verbose: bool) -> None:
    """Draw a circle on every frame of a video."""
    # Debug calls cost next to nothing when filtered out
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG if verbose else logging.INFO))


main.add_command(run)
main.add_command(blur)
main.add_command(export_onnx)
main.add_command(export_safetensors)
main.add_command(export_tensorrt)

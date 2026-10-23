from pathlib import Path

import click
import structlog

from ...transforms import Color
from ...transforms import export_onnx as export_onnx_model
from .options import color_option

log = structlog.get_logger()


@click.command(name="export-onnx")
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@color_option
def export_onnx(output_path: Path, color: Color) -> None:
    """Export the circle-drawing model to OUTPUT_PATH, in ONNX format (the radius is chosen with `run --radius`)."""
    export_onnx_model(output_path, color=color)
    log.info("onnx.exported", path=str(output_path), color=color)

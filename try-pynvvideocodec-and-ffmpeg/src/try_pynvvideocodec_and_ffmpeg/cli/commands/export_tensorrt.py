from pathlib import Path

import click
import structlog

from ...transforms import Color
from ...transforms import export_tensorrt as export_tensorrt_engine
from .options import color_option

log = structlog.get_logger()


@click.command(name="export-tensorrt")
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@color_option
def export_tensorrt(output_path: Path, color: Color) -> None:
    """Build a TensorRT engine of the circle-drawing model to OUTPUT_PATH, for this GPU only."""
    export_tensorrt_engine(output_path, color=color)
    log.info("tensorrt.exported", path=str(output_path), color=color)

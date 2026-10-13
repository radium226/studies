from pathlib import Path

import click
import structlog

from ...transforms import Color
from ...transforms import export_safetensors as export_safetensors_weights
from .options import color_option

log = structlog.get_logger()


@click.command(name="export-safetensors")
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@color_option
def export_safetensors(output_path: Path, color: Color) -> None:
    """Export the circle-drawing model's weights (its color) to OUTPUT_PATH, in SafeTensors format."""
    export_safetensors_weights(output_path, color=color)
    log.info("safetensors.exported", path=str(output_path), color=color)

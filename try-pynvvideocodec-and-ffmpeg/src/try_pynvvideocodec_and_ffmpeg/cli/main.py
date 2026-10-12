import click

from .commands import export_onnx, export_safetensors, run


@click.group()
def main() -> None:
    """Draw a circle on every frame of a video."""


main.add_command(run)
main.add_command(export_onnx)
main.add_command(export_safetensors)

import click

from ...transforms import DEFAULT_COLOR

color_option = click.option(
    "--color",
    type=(click.IntRange(0, 255), click.IntRange(0, 255), click.IntRange(0, 255)),
    default=DEFAULT_COLOR,
    show_default=True,
    metavar="R G B",
    help="Circle color, stored in the exported file.",
)

"""Command-line interface: `depforecast scan | explain | doctor`."""

import typer

from depforecast import __version__

app = typer.Typer(
    name="depforecast",
    help="A weather forecast for your dependencies.",
    no_args_is_help=True,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"depforecast {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Predict which npm dependencies are likely to be abandoned in the next 12 months."""

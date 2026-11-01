"""Command-line interface: `depforecast scan | explain | doctor`."""

import typer
from rich.console import Console

from depforecast import __version__
from depforecast.config import load_settings
from depforecast.doctor import Level, run_checks

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


_ICONS = {Level.OK: "[green]✅[/]", Level.WARN: "[yellow]⚠️ [/]", Level.FAIL: "[red]❌[/]"}


@app.command()
def doctor() -> None:
    """Check GitHub token, cache, TabPFN token/weights and Ollama."""
    console = Console()
    checks = run_checks(load_settings())
    for check in checks:
        console.print(f"{_ICONS[check.level]} [bold]{check.name}[/]: {check.detail}")
    if any(c.level is Level.FAIL for c in checks):
        raise typer.Exit(code=1)

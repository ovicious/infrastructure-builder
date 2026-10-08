"""Command-line interface for Infrastructure Builder."""

import typer

from infra_builder import __version__

app = typer.Typer(
    name="infra-builder",
    help="Plan and generate infrastructure safely.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Plan and generate infrastructure safely."""


@app.command()
def version() -> None:
    """Show the installed version."""
    typer.echo(__version__)

"""Command-line interface for Infrastructure Builder."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from infra_builder import __version__
from infra_builder.config_io import ConfigError, load_config, save_config
from infra_builder.validator import validate_config
from infra_builder.wizard import collect_config, default_config

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


@app.command()
def init(
    output: Annotated[Path, typer.Option("--output")] = Path("infra.yaml"),
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
) -> None:
    """Create a versioned configuration without deploying anything."""
    try:
        config = default_config() if non_interactive else collect_config()
        validate_config(config, output.parent)
        if output.exists() or output.is_symlink():
            raise ConfigError(f"config file already exists: '{output}'")
        output.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, output)
    except (ConfigError, ValidationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except OSError as error:
        typer.echo(f"cannot create config '{output}': {error.strerror}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Configuration written to {output}")
    typer.echo("No infrastructure changes have been made.")


@app.command()
def validate(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
) -> None:
    """Validate a configuration file without making system changes."""
    try:
        config = load_config(config_path)
        validate_config(config, config_path.parent)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(f"Configuration valid: {config_path}")

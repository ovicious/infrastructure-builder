"""Interactive configuration wizard for supported local targets."""

from pathlib import Path

import typer
from pydantic import ValidationError

from infra_builder.config_io import ConfigError
from infra_builder.models import (
    ApplicationConfig,
    InfraConfig,
    ObservabilityConfig,
    PostgresConfig,
    ProjectConfig,
    RedisConfig,
    RuntimeConfig,
    ServicesConfig,
)
from infra_builder.validator import validate_config


def _choose(label: str, options: list[str], default: str) -> str:
    typer.echo(label)
    for index, option in enumerate(options, start=1):
        suffix = " (default)" if option == default else ""
        typer.echo(f"  {index}. {option}{suffix}")
    while True:
        answer = typer.prompt(
            "Select an option",
            default=str(options.index(default) + 1),
        )
        try:
            selected = int(answer)
            if 1 <= selected <= len(options):
                return options[selected - 1]
        except ValueError:
            if answer in options:
                return answer
        typer.echo(f"Choose a number from 1 to {len(options)}.")


def collect_config(config_dir: Path | None = None) -> InfraConfig:
    """Prompt for the supported MVP configuration options."""
    config_dir = config_dir or Path.cwd()
    name = typer.prompt("Project name", default="infra-project")
    project = ProjectConfig(name=name)
    runtime = _choose(
        "How should the application run?",
        ["docker_compose", "host_systemd"],
        "docker_compose",
    )
    app_type = _choose(
        "Application type",
        ["generic", "python", "go", "static"],
        "generic",
    )
    app_name = typer.prompt("Application service name", default="app")
    port = typer.prompt("Application port", default=8080, type=int)
    image = None
    if runtime == "docker_compose":
        image_default = "nginx:stable" if app_type == "generic" else ""
        image = typer.prompt("Container image", default=image_default)
    while True:
        output_dir = typer.prompt(
            "Project output directory (relative to the config directory)",
            default=f"./generated/{name}",
        )
        try:
            project = ProjectConfig(name=name, output_dir=output_dir)
            validate_config(InfraConfig(project=project), config_dir)
            break
        except ValidationError:
            typer.echo(
                "Choose a relative path without '..' segments, for example "
                f"'./generated/{name}'."
            )
        except ConfigError as error:
            typer.echo(str(error))

    postgres = typer.confirm("Include PostgreSQL?", default=False)
    redis = typer.confirm("Include Redis?", default=False)
    metrics = typer.confirm("Include metrics and monitoring?", default=False)

    return InfraConfig(
        project=project,
        runtime=RuntimeConfig(type=runtime),
        application=ApplicationConfig(
            type=app_type,
            name=app_name,
            port=port,
            image=image or None,
        ),
        services=ServicesConfig(
            postgres=PostgresConfig(enabled=postgres),
            redis=RedisConfig(enabled=redis),
        ),
        observability=ObservabilityConfig(metrics=metrics),
    )


def default_config() -> InfraConfig:
    """Return a safe default config for non-interactive initialization."""
    return InfraConfig()

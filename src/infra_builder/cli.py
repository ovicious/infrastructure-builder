"""Command-line interface for Infrastructure Builder."""

from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError

from infra_builder import __version__
from infra_builder.adapters.docker_compose import (
    AdapterError,
    DockerComposeAdapter,
    MissingPrerequisiteError,
    RuntimeStatus,
)
from infra_builder.config_io import ConfigError, load_config, save_config
from infra_builder.generator import GenerationError, generate_files
from infra_builder.models import InfraConfig
from infra_builder.ollama import OllamaAssistant, OllamaError
from infra_builder.planner import format_plan_json, format_plan_text
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


@app.command("ai-configure")
def ai_configure(
    prompt: Annotated[
        str | None,
        typer.Argument(help="Describe the infrastructure you want to generate."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Save the validated draft to a YAML file."),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", help="Ollama model name to use."),
    ] = "qwen2.5:1.5b",
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="Ollama base URL."),
    ] = "http://127.0.0.1:11434",
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Ollama request timeout in seconds."),
    ] = 30.0,
) -> None:
    """Generate a validated configuration draft using a local Ollama model."""
    try:
        config_prompt = prompt or typer.prompt(
            "Describe the infrastructure to generate",
            default=(
                "Create a local Docker Compose app with PostgreSQL, Redis, and "
                "prometheus metrics for a Python service on port 8080."
            ),
        )
        config = OllamaAssistant(
            model=model,
            base_url=base_url,
            timeout=timeout,
        ).generate_config(config_prompt)
        validate_config(config, Path.cwd())
    except (ConfigError, ValidationError, OllamaError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=3 if isinstance(error, OllamaError) else 2) from error
    except OSError as error:
        typer.echo(f"cannot generate config: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("Generated configuration preview:")
    typer.echo(config.model_dump_json(indent=2))

    if output is None:
        return

    try:
        if output.exists() or output.is_symlink():
            raise ConfigError(f"config file already exists: '{output}'")
        output.parent.mkdir(parents=True, exist_ok=True)
    except (ConfigError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    if not typer.confirm(f"Save this configuration to {output}?"):
        typer.echo("AI configuration review cancelled; no file was written.")
        raise typer.Exit(code=4)

    try:
        save_config(config, output)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(f"Configuration written to {output}")


@app.command()
def init(
    output: Annotated[Path, typer.Option("--output")] = Path("infra.yaml"),
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
) -> None:
    """Create a versioned configuration without deploying anything."""
    try:
        config = (
            default_config()
            if non_interactive
            else collect_config(config_dir=output.parent)
        )
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
    except (ConfigError, ValidationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(f"Configuration valid: {config_path}")


@app.command()
def plan(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
    output_format: Annotated[
        Literal["text", "json"], typer.Option("--format")
    ] = "text",
) -> None:
    """Show the deterministic file-generation plan without making changes."""
    try:
        config = load_config(config_path)
        validate_config(config, config_path.parent)
    except (ConfigError, ValidationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(
        format_plan_json(config)
        if output_format == "json"
        else format_plan_text(config)
    )


@app.command()
def generate(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Override output directory relative to CONFIG."),
    ] = None,
) -> None:
    """Generate planned project files; does not start services."""
    try:
        config = load_config(config_path)
        if output is not None:
            config_data = config.model_dump(mode="python")
            config_data["project"]["output_dir"] = output.as_posix()
            config = InfraConfig.model_validate(config_data)
        validate_config(config, config_path.parent)
        generated = generate_files(config, config_path.parent)
    except (ConfigError, ValidationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except GenerationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except OSError as error:
        typer.echo(f"cannot generate project files: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("Generated files:")
    for path in generated:
        typer.echo(f"  {path}")
    typer.echo("No services were started.")


def _load_runtime_config(config_path: Path) -> InfraConfig:
    config = load_config(config_path)
    validate_config(config, config_path.parent)
    return config


def _display_status(status: RuntimeStatus) -> None:
    if not status.managed:
        typer.echo(status.lifecycle)
        return
    typer.echo(f"Managed runtime: {status.lifecycle}")
    if not status.services:
        typer.echo("No containers found for the managed Compose project.")
        return
    for service in status.services:
        health = (
            f", health: {service.health}"
            if service.health not in {"", "not reported"}
            else ", healthcheck: not configured"
        )
        typer.echo(f"  {service.name}: {service.state}{health}")


def _display_apply_plan(config: InfraConfig) -> None:
    typer.echo(format_plan_text(config))
    services = sorted(
        {
            config.application.name,
            *({"postgres"} if config.services.postgres.enabled else set()),
            *({"redis"} if config.services.redis.enabled else set()),
            *({"prometheus"} if config.observability.metrics else set()),
        }
    )
    typer.echo("After approval, apply will:")
    typer.echo(
        f"  [local_change] Start Compose project {config.project.name}: "
        f"{', '.join(services)}"
    )
    typer.echo(
        "  [reversible] Run the tool-built `docker compose up --detach` command; "
        "destroy stops containers and retains named volumes."
    )


@app.command()
def apply(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Review the plan and start supported local Compose services."""
    try:
        config = _load_runtime_config(config_path)
        if config.runtime.type != "docker_compose":
            typer.echo("apply currently supports only docker_compose.", err=True)
            raise typer.Exit(code=2)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    _display_apply_plan(config)
    if not yes and not typer.confirm(
        "Apply this plan and start the Docker Compose services?", default=False
    ):
        typer.echo("Apply cancelled; no changes were made.")
        raise typer.Exit(code=4)

    try:
        status = DockerComposeAdapter().apply(config, config_path.parent)
    except AdapterError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(
            code=3 if isinstance(error, MissingPrerequisiteError) else 5
        ) from error
    except GenerationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=5) from error
    except OSError as error:
        typer.echo(f"apply failed: {error}", err=True)
        raise typer.Exit(code=5) from error

    _display_status(status)


@app.command()
def status(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
) -> None:
    """Report status of resources recorded as tool-managed."""
    try:
        config = _load_runtime_config(config_path)
        result = DockerComposeAdapter().status(config, config_path.parent)
    except (ConfigError, AdapterError, GenerationError) as error:
        typer.echo(str(error), err=True)
        code = (
            3
            if isinstance(error, MissingPrerequisiteError)
            else 5
            if isinstance(error, (AdapterError, GenerationError))
            else 2
        )
        raise typer.Exit(code=code) from error
    except OSError as error:
        typer.echo(f"status check failed: {error}", err=True)
        raise typer.Exit(code=5) from error

    _display_status(result)


@app.command()
def destroy(
    config_path: Annotated[Path, typer.Argument(metavar="CONFIG")] = Path("infra.yaml"),
    yes: Annotated[bool, typer.Option("--yes")] = False,
    confirm_project: Annotated[
        str | None,
        typer.Option(
            "--confirm-project",
            help="Required with --yes; must exactly match the project name.",
        ),
    ] = None,
) -> None:
    """Show and confirm cleanup of only resources recorded as tool-managed."""
    try:
        config = _load_runtime_config(config_path)
        if config.runtime.type != "docker_compose":
            typer.echo("destroy is supported only for docker_compose.", err=True)
            raise typer.Exit(code=2)
        adapter = DockerComposeAdapter()
        preview = adapter.destroy_preview(config, config_path.parent)
    except (ConfigError, AdapterError, GenerationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(
            code=5 if isinstance(error, (AdapterError, GenerationError)) else 2
        ) from error

    if not preview:
        typer.echo("No infra-builder-managed resources found.")
        return

    typer.echo("Destroy will:")
    for item in preview:
        typer.echo(f"  {item}")

    if yes:
        if confirm_project != config.project.name:
            typer.echo(
                "Destructive non-interactive execution requires "
                f"--confirm-project {config.project.name}.",
                err=True,
            )
            raise typer.Exit(code=4)
    else:
        if confirm_project is not None:
            typer.echo("--confirm-project is valid only together with --yes.", err=True)
            raise typer.Exit(code=2)
        entered = typer.prompt(
            f"Type the project name '{config.project.name}' to confirm destruction"
        )
        if entered != config.project.name:
            typer.echo("Destroy cancelled; no resources were changed.")
            raise typer.Exit(code=4)

    try:
        result = adapter.destroy(config, config_path.parent)
    except (AdapterError, GenerationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(
            code=3 if isinstance(error, MissingPrerequisiteError) else 5
        ) from error
    except OSError as error:
        typer.echo(f"destroy failed: {error}", err=True)
        raise typer.Exit(code=5) from error
    for item in result:
        typer.echo(item)

from typer.testing import CliRunner

from infra_builder import __version__
from infra_builder.cli import app
from infra_builder.config_io import load_config

runner = CliRunner()


def test_help_lists_version_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "version" in result.stdout


def test_version_displays_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_non_interactive_init_writes_default_config(tmp_path) -> None:
    output = tmp_path / "infra.yaml"

    result = runner.invoke(
        app,
        ["init", "--non-interactive", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert "No infrastructure changes have been made." in result.stdout
    assert load_config(output).project.name == "infra-project"


def test_init_does_not_overwrite_an_existing_config(tmp_path) -> None:
    output = tmp_path / "infra.yaml"
    output.write_text("keep this file", encoding="utf-8")

    result = runner.invoke(
        app,
        ["init", "--non-interactive", "--output", str(output)],
    )

    assert result.exit_code == 2
    assert "already exists" in result.stderr
    assert output.read_text(encoding="utf-8") == "keep this file"


def test_interactive_init_collects_local_configuration(tmp_path) -> None:
    output = tmp_path / "infra.yaml"
    answers = "\n".join(
        [
            "demo-api",
            "1",
            "1",
            "api",
            "8080",
            "nginx:stable",
            "./generated/demo-api",
            "y",
            "n",
            "y",
        ]
    )

    result = runner.invoke(
        app,
        ["init", "--output", str(output)],
        input=answers,
    )

    assert result.exit_code == 0, result.output
    config = load_config(output)
    assert config.project.name == "demo-api"
    assert config.services.postgres.enabled is True
    assert config.observability.metrics is True


def test_validate_reports_invalid_config(tmp_path) -> None:
    config = tmp_path / "infra.yaml"
    config.write_text("schema_version: 42\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(config)])

    assert result.exit_code == 2
    assert "schema_version" in result.stderr

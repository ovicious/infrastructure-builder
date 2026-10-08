from typer.testing import CliRunner

from infra_builder import __version__
from infra_builder.cli import app

runner = CliRunner()


def test_help_lists_version_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "version" in result.stdout


def test_version_displays_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__

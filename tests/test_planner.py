import json
from pathlib import Path

from infra_builder.models import InfraConfig
from infra_builder.planner import (
    build_plan,
    format_plan_json,
    format_plan_text,
    planned_files,
)


def test_plan_is_deterministic() -> None:
    config = InfraConfig(
        project={"name": "demo-api", "output_dir": "./generated/demo-api"},
        services={"postgres": {"enabled": True}, "redis": {"enabled": True}},
        observability={"metrics": True},
    )

    assert build_plan(config) == build_plan(config)
    assert build_plan(config)[-1].depends_on == (
        "generate-01",
        "generate-02",
        "generate-03",
    )
    assert planned_files(config) == (
        "compose.yaml",
        ".env.example",
        "monitoring/prometheus.yml",
        ".infra-builder-manifest.json",
    )
    assert format_plan_json(config) == format_plan_json(config)
    assert "No changes have been made." in format_plan_text(config)
    assert "services: app, postgres, redis, prometheus" in format_plan_text(config)
    assert json.loads(format_plan_json(config))["project"] == "demo-api"


def test_host_runtime_plan_contains_only_a_unit_template() -> None:
    config = InfraConfig(
        runtime={"type": "host_systemd"},
        application={"type": "python", "image": None},
    )

    assert planned_files(config) == (
        "systemd/app.service",
        ".infra-builder-manifest.json",
    )


def test_cli_plan_does_not_create_output_files(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from infra_builder.cli import app
    from infra_builder.config_io import save_config

    config_path = tmp_path / "infra.yaml"
    save_config(
        InfraConfig(project={"output_dir": "./generated/demo"}),
        config_path,
    )
    output_dir = tmp_path / "generated" / "demo"

    result = CliRunner().invoke(app, ["plan", str(config_path), "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["actions"]
    assert not output_dir.exists()

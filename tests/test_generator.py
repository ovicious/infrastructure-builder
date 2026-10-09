import hashlib
import json
from pathlib import Path

import pytest
import yaml

from infra_builder.generator import GenerationError, generate_files, render_files
from infra_builder.models import InfraConfig
from infra_builder.planner import MANIFEST_NAME


def compose_config() -> InfraConfig:
    return InfraConfig(
        project={"name": "demo-api", "output_dir": "./generated/demo-api"},
        application={"name": "api", "port": 8080, "image": "nginx:stable"},
        services={
            "postgres": {"enabled": True},
            "redis": {"enabled": True},
        },
        observability={"metrics": True},
    )


def test_render_files_contains_no_real_secrets_and_has_valid_yaml() -> None:
    config = compose_config()
    files = render_files(config)
    compose = yaml.safe_load(files["compose.yaml"])
    prometheus = yaml.safe_load(files["monitoring/prometheus.yml"])

    assert set(compose["services"]) == {"api", "postgres", "redis", "prometheus"}
    assert compose["services"]["api"]["ports"] == ["127.0.0.1:8080:8080"]
    assert (
        compose["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
        == "${INFRA_BUILDER_POSTGRES_PASSWORD:?Set "
        "INFRA_BUILDER_POSTGRES_PASSWORD before starting services}"
    )
    assert b"replace-me" not in files[".env.example"]
    assert all(line.startswith(b"#") for line in files[".env.example"].splitlines())
    assert prometheus["scrape_configs"][0]["static_configs"][0]["targets"] == [
        "api:8080"
    ]


def test_generate_creates_deterministic_files_and_manifest(tmp_path: Path) -> None:
    config = compose_config()
    generated = generate_files(config, tmp_path)
    output_dir = tmp_path / config.project.output_dir
    manifest = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))

    assert all(path.exists() for path in generated)
    assert set(manifest["managed_files"]) == {
        "compose.yaml",
        ".env.example",
        "monitoring/prometheus.yml",
    }
    for name, digest in manifest["managed_files"].items():
        contents = (output_dir / name).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == digest

    first_contents = {path.name: path.read_bytes() for path in generated}
    generate_files(config, tmp_path)
    assert first_contents == {path.name: path.read_bytes() for path in generated}


def test_generate_does_not_overwrite_unmanaged_file(tmp_path: Path) -> None:
    config = compose_config()
    output_dir = tmp_path / config.project.output_dir
    output_dir.mkdir(parents=True)
    compose_path = output_dir / "compose.yaml"
    compose_path.write_text("user-owned file\n", encoding="utf-8")

    with pytest.raises(GenerationError, match="unmanaged file"):
        generate_files(config, tmp_path)

    assert compose_path.read_text(encoding="utf-8") == "user-owned file\n"
    assert not (output_dir / MANIFEST_NAME).exists()


def test_generate_refuses_modified_managed_file(tmp_path: Path) -> None:
    config = compose_config()
    generate_files(config, tmp_path)
    compose_path = tmp_path / config.project.output_dir / "compose.yaml"
    compose_path.write_text("manually changed\n", encoding="utf-8")

    with pytest.raises(GenerationError, match="was modified"):
        generate_files(config, tmp_path)

    assert compose_path.read_text(encoding="utf-8") == "manually changed\n"


def test_generate_refuses_corrupt_manifest(tmp_path: Path) -> None:
    config = compose_config()
    output_dir = tmp_path / config.project.output_dir
    output_dir.mkdir(parents=True)
    (output_dir / MANIFEST_NAME).write_text("{invalid", encoding="utf-8")

    with pytest.raises(GenerationError, match="manual review"):
        generate_files(config, tmp_path)


def test_generate_refuses_symlinked_generated_file(tmp_path: Path) -> None:
    config = InfraConfig(
        project={"name": "demo-api", "output_dir": "./generated/demo-api"}
    )
    output_dir = tmp_path / config.project.output_dir
    output_dir.mkdir(parents=True)
    external_file = tmp_path / "external.yaml"
    external_file.write_text("keep", encoding="utf-8")
    (output_dir / "compose.yaml").symlink_to(external_file)

    with pytest.raises(GenerationError, match="symlink"):
        generate_files(config, tmp_path)

    assert external_file.read_text(encoding="utf-8") == "keep"


def test_generate_refuses_symlinked_generated_directory(tmp_path: Path) -> None:
    config = compose_config()
    output_dir = tmp_path / config.project.output_dir
    output_dir.mkdir(parents=True)
    external_dir = tmp_path / "external-monitoring"
    external_dir.mkdir()
    (output_dir / "monitoring").symlink_to(external_dir, target_is_directory=True)

    with pytest.raises(GenerationError, match="escapes"):
        generate_files(config, tmp_path)

    assert list(external_dir.iterdir()) == []
    assert not (output_dir / "compose.yaml").exists()


def test_generate_reports_symlink_loops_without_partial_files(tmp_path: Path) -> None:
    config = compose_config()
    output_dir = tmp_path / config.project.output_dir
    output_dir.mkdir(parents=True)
    (output_dir / "monitoring").symlink_to("monitoring", target_is_directory=True)

    with pytest.raises(GenerationError, match="cannot safely resolve"):
        generate_files(config, tmp_path)

    assert not (output_dir / "compose.yaml").exists()


def test_generate_host_systemd_only_writes_reviewable_template(tmp_path: Path) -> None:
    config = InfraConfig(
        project={"name": "demo-api", "output_dir": "./generated/demo-api"},
        runtime={"type": "host_systemd"},
        application={"type": "python", "image": None},
    )

    generated = generate_files(config, tmp_path)
    unit = (tmp_path / config.project.output_dir / "systemd/app.service").read_text(
        encoding="utf-8"
    )

    assert len(generated) == 2
    assert "Template only" in unit
    assert "not installed, enabled, or started" in unit

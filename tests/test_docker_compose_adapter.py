import json
import subprocess
from pathlib import Path

import pytest

from infra_builder.adapters.docker_compose import (
    AdapterError,
    DockerComposeAdapter,
    MissingPrerequisiteError,
)
from infra_builder.generator import GenerationError, generate_files
from infra_builder.models import InfraConfig
from infra_builder.state import STATE_NAME, load_state


def postgres_config() -> InfraConfig:
    return InfraConfig(
        project={"name": "adapter-test", "output_dir": "./generated/adapter-test"},
        application={"name": "api", "image": "nginx:stable"},
        services={"postgres": {"enabled": True}},
    )


def install_fake_docker(monkeypatch, *, up_returncode: int = 0) -> list[list[str]]:
    commands: list[list[str]] = []
    ps_calls = 0

    def fake_which(name: str) -> str | None:
        return "/usr/bin/docker" if name == "docker" else None

    def fake_run(args, **kwargs):
        nonlocal ps_calls
        commands.append(args)
        assert kwargs["shell"] is False
        assert kwargs["timeout"] > 0
        if args == ["docker", "--version"] or args == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(args, 0, "Docker version test", "")
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "ps" in args:
            ps_calls += 1
            output = json.dumps(
                [
                    {"Service": "api", "State": "running"},
                    {
                        "Service": "postgres",
                        "State": "running",
                        "Health": "healthy",
                    },
                ]
            )
            return subprocess.CompletedProcess(args, 0, output, "")
        if "up" in args:
            return subprocess.CompletedProcess(args, up_returncode, "", "redacted")
        if "down" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.shutil.which", fake_which
    )
    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.subprocess.run", fake_run
    )
    return commands


def test_apply_records_managed_state_after_running_and_health_checks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    config = postgres_config()

    result = DockerComposeAdapter().apply(config, tmp_path)

    output = tmp_path / config.project.output_dir
    state = load_state(output, config.project.name)
    assert result.managed is True
    assert result.lifecycle == "applied"
    assert state is not None and state.status == "applied"
    assert any("up" in command for command in commands)
    assert all("test-only-secret" not in " ".join(command) for command in commands)


def test_apply_accepts_previously_generated_managed_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    config = postgres_config()
    generate_files(config, tmp_path)

    DockerComposeAdapter().apply(config, tmp_path)

    assert any("up" in command for command in commands)


def test_generate_refuses_config_changes_while_runtime_is_managed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    install_fake_docker(monkeypatch)
    config = postgres_config()
    DockerComposeAdapter().apply(config, tmp_path)
    changed_data = config.model_dump(mode="python")
    changed_data["application"]["port"] = 8081
    changed_config = InfraConfig.model_validate(changed_data)

    with pytest.raises(GenerationError, match="original config"):
        generate_files(changed_config, tmp_path)

    state = load_state(tmp_path / config.project.output_dir, config.project.name)
    assert state is not None and state.status == "applied"


def test_apply_refuses_unmanaged_compose_file(tmp_path: Path, monkeypatch) -> None:
    commands = install_fake_docker(monkeypatch)
    config = InfraConfig()
    output = tmp_path / config.project.output_dir
    output.mkdir(parents=True)
    (output / "compose.yaml").write_text("user-owned\n", encoding="utf-8")

    with pytest.raises(GenerationError, match="unmanaged file"):
        DockerComposeAdapter().apply(config, tmp_path)

    assert not any("up" in command for command in commands)
    assert not (output / STATE_NAME).exists()


def test_apply_refuses_existing_unmanaged_compose_resources(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    original_run = subprocess.run

    def existing_project_run(args, **kwargs):
        result = original_run(args, **kwargs)
        if args[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(args, 0, "unmanaged-id\n", "")
        return result

    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.subprocess.run", existing_project_run
    )
    config = postgres_config()

    with pytest.raises(AdapterError, match="refusing to adopt them"):
        DockerComposeAdapter().apply(config, tmp_path)

    assert not any("up" in command for command in commands)
    assert not (tmp_path / config.project.output_dir / STATE_NAME).exists()


def test_apply_failure_records_partial_stack_for_review_and_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    install_fake_docker(monkeypatch, up_returncode=17)
    config = postgres_config()

    with pytest.raises(AdapterError, match="partial resources may exist"):
        DockerComposeAdapter().apply(config, tmp_path)

    state = load_state(tmp_path / config.project.output_dir, config.project.name)
    assert state is not None and state.status == "apply_failed"


def test_destroy_failure_preserves_state_and_files_for_retry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    failed_commands: list[list[str]] = []
    config = postgres_config()
    adapter = DockerComposeAdapter()
    adapter.apply(config, tmp_path)

    def fail_down(args, **kwargs):
        if "down" in args:
            failed_commands.append(args)
            return subprocess.CompletedProcess(args, 17, "", "redacted")
        return original_run(args, **kwargs)

    original_run = subprocess.run
    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.subprocess.run", fail_down
    )
    output = tmp_path / config.project.output_dir

    with pytest.raises(AdapterError, match="resources remain recorded"):
        adapter.destroy(config, tmp_path)

    state = load_state(output, config.project.name)
    assert state is not None and state.status == "destroy_failed"
    assert (output / "compose.yaml").is_file()
    assert not (output / ".infra-builder-manifest.json").is_symlink()
    assert any("down" in command for command in failed_commands)
    assert commands


def test_apply_health_failure_is_recorded_for_manual_review(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)

    def unhealthy_run(args, **kwargs):
        result = next_run(args, **kwargs)
        if "ps" in args and args[:2] != ["docker", "ps"]:
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [
                        {"Service": "api", "State": "running"},
                        {
                            "Service": "postgres",
                            "State": "exited",
                            "Health": "unhealthy",
                        },
                    ]
                ),
                "",
            )
        return result

    next_run = subprocess.run
    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.subprocess.run", unhealthy_run
    )
    config = postgres_config()

    with pytest.raises(AdapterError, match="health check failed"):
        DockerComposeAdapter().apply(config, tmp_path)

    state = load_state(tmp_path / config.project.output_dir, config.project.name)
    assert state is not None and state.status == "apply_failed"
    assert any("up" in command for command in commands)


def test_missing_docker_fails_before_generating_or_recording_state(
    tmp_path: Path, monkeypatch
) -> None:
    config = InfraConfig()
    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.shutil.which", lambda _: None
    )

    with pytest.raises(MissingPrerequisiteError, match="Docker is not installed"):
        DockerComposeAdapter().apply(config, tmp_path)

    output = tmp_path / config.project.output_dir
    assert not output.exists()
    assert not (output / STATE_NAME).exists()


def test_postgres_requires_an_environment_secret_before_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("INFRA_BUILDER_POSTGRES_PASSWORD", raising=False)
    commands = install_fake_docker(monkeypatch)

    with pytest.raises(
        MissingPrerequisiteError, match="INFRA_BUILDER_POSTGRES_PASSWORD"
    ):
        DockerComposeAdapter().apply(postgres_config(), tmp_path)

    assert not any("up" in command for command in commands)
    assert not (tmp_path / postgres_config().project.output_dir).exists()


def test_status_does_not_inspect_unmanaged_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "infra_builder.adapters.docker_compose.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not call Docker for unmanaged project")
        ),
    )

    result = DockerComposeAdapter().status(InfraConfig(), tmp_path)

    assert result.managed is False
    assert "no resources are managed" in result.lifecycle


def test_destroy_stops_stack_and_removes_only_managed_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    config = postgres_config()
    adapter = DockerComposeAdapter()
    adapter.apply(config, tmp_path)

    preview = adapter.destroy_preview(config, tmp_path)
    result = adapter.destroy(config, tmp_path)

    output = tmp_path / config.project.output_dir
    down_command = next(command for command in commands if "down" in command)
    assert any("Services: api, postgres" in line for line in preview)
    assert any(".infra-builder-state.json" in line for line in preview)
    assert "down" in down_command
    assert "--volumes" not in down_command
    assert result[0] == "Stopped Compose project adapter-test."
    assert not (output / "compose.yaml").exists()
    assert not (output / ".infra-builder-manifest.json").exists()
    assert not (output / STATE_NAME).exists()


def test_destroy_refuses_modified_managed_files_before_stopping(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    commands = install_fake_docker(monkeypatch)
    config = postgres_config()
    adapter = DockerComposeAdapter()
    adapter.apply(config, tmp_path)
    compose = tmp_path / config.project.output_dir / "compose.yaml"
    compose.write_text("user edit\n", encoding="utf-8")

    with pytest.raises(AdapterError, match="was modified"):
        adapter.destroy(config, tmp_path)

    assert not any("down" in command for command in commands)


def test_status_checks_only_managed_stack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INFRA_BUILDER_POSTGRES_PASSWORD", "test-only-secret")
    install_fake_docker(monkeypatch)
    config = postgres_config()
    adapter = DockerComposeAdapter()
    adapter.apply(config, tmp_path)

    result = adapter.status(config, tmp_path)

    assert result.managed is True
    assert result.lifecycle == "applied"
    assert {service.name for service in result.services} == {"api", "postgres"}

"""Approval-gated local Docker Compose operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infra_builder.generator import generate_files
from infra_builder.models import InfraConfig
from infra_builder.planner import MANIFEST_NAME, planned_files
from infra_builder.state import ManagedState, load_state, remove_state, save_state

COMMAND_TIMEOUT_SECONDS = 120


class AdapterError(RuntimeError):
    """Raised when a local adapter cannot safely complete an operation."""


class MissingPrerequisiteError(AdapterError):
    """Raised when Docker or required operator-provided environment is missing."""


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    health: str


@dataclass(frozen=True)
class RuntimeStatus:
    managed: bool
    lifecycle: str
    services: tuple[ServiceStatus, ...] = ()


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _config_sha256(config: InfraConfig) -> str:
    canonical = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(canonical)


def _output_dir(config: InfraConfig, config_dir: Path) -> Path:
    root = config_dir.resolve()
    output = (root / config.project.output_dir).resolve()
    if not output.is_relative_to(root):
        raise AdapterError("project output directory escapes the config directory")
    return output


def _load_generated_manifest(
    config: InfraConfig,
    config_dir: Path,
) -> tuple[Path, dict[str, Any], str, str]:
    output = _output_dir(config, config_dir)
    manifest_path = output / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise AdapterError(
            "generated project files are missing or unmanaged; "
            "run 'infra-builder generate' first"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AdapterError(
            f"managed manifest '{manifest_path}' is unreadable; "
            "manual review is required"
        ) from error
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or manifest.get("project_name") != config.project.name
        or manifest.get("config_sha256") != _config_sha256(config)
        or not isinstance(manifest.get("managed_files"), dict)
    ):
        raise AdapterError(
            f"managed manifest '{manifest_path}' does not match this config; "
            "run 'infra-builder generate' and review the result"
        )

    files = manifest["managed_files"]
    expected_files = set(planned_files(config)) - {MANIFEST_NAME}
    if set(files) != expected_files:
        raise AdapterError(
            f"managed manifest '{manifest_path}' does not match expected files; "
            "manual review is required"
        )
    config_hash = manifest["config_sha256"]
    if not isinstance(config_hash, str) or len(config_hash) != 64:
        raise AdapterError(
            f"managed manifest '{manifest_path}' has an invalid config hash"
        )
    compose_hash = files.get("compose.yaml")
    if (
        not isinstance(compose_hash, str)
        or len(compose_hash) != 64
        or any(char not in "0123456789abcdef" for char in compose_hash)
    ):
        raise AdapterError("managed manifest does not own compose.yaml")
    for relative_path, expected_hash in files.items():
        if (
            not isinstance(relative_path, str)
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)
        ):
            raise AdapterError("managed manifest contains an invalid file entry")
        candidate = output / relative_path
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as error:
            raise AdapterError(
                f"cannot safely resolve managed file '{relative_path}'"
            ) from error
        if candidate.is_symlink() or not resolved.is_relative_to(output):
            raise AdapterError(
                f"managed file '{relative_path}' is missing or unsafe; "
                "manual review is required"
            )
        if not candidate.is_file():
            raise AdapterError(
                f"managed file '{relative_path}' is missing or unsafe; "
                "manual review is required"
            )
        try:
            actual_hash = _sha256(candidate.read_bytes())
        except OSError as error:
            raise AdapterError(f"cannot verify managed file '{candidate}'") from error
        if actual_hash != expected_hash:
            raise AdapterError(
                f"managed file '{candidate}' was modified; manual review is required"
            )
    return output, manifest, compose_hash, config_hash


def _expected_services(config: InfraConfig) -> set[str]:
    services = {config.application.name}
    if config.services.postgres.enabled:
        services.add("postgres")
    if config.services.redis.enabled:
        services.add("redis")
    if config.observability.metrics:
        services.add("prometheus")
    return services


def _parse_compose_ps(output: str) -> tuple[ServiceStatus, ...]:
    if not output.strip():
        return ()
    try:
        decoded: Any = json.loads(output)
        records = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        records = []
        try:
            records = [json.loads(line) for line in output.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise AdapterError(
                "Docker Compose returned invalid service status JSON"
            ) from error

    services: list[ServiceStatus] = []
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("Service"), str):
            raise AdapterError("Docker Compose returned an unexpected service status")
        services.append(
            ServiceStatus(
                name=item["Service"],
                state=str(item.get("State", "unknown")).lower(),
                health=str(item.get("Health", "not reported")).lower(),
            )
        )
    return tuple(sorted(services, key=lambda service: service.name))


class DockerComposeAdapter:
    """Own only the named Compose project recorded in the local state file."""

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as error:
            raise MissingPrerequisiteError(
                "Docker is not installed or is not available on PATH; "
                "install Docker Desktop/Engine with the Compose plugin"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise AdapterError(
                f"command timed out after {COMMAND_TIMEOUT_SECONDS} seconds"
            ) from error
        except OSError as error:
            raise AdapterError(f"could not run Docker command: {error}") from error
        return result

    def _require_docker(self) -> None:
        if shutil.which("docker") is None:
            raise MissingPrerequisiteError(
                "Docker is not installed or is not available on PATH; "
                "install Docker Engine/Desktop with the Compose v2 plugin"
            )
        for args, description in (
            (["docker", "--version"], "Docker CLI"),
            (["docker", "compose", "version"], "Docker Compose v2"),
        ):
            result = self._run(args, Path.cwd())
            if result.returncode != 0:
                raise MissingPrerequisiteError(
                    f"{description} prerequisite check failed "
                    f"(exit code {result.returncode})"
                )

    @staticmethod
    def _require_config_secrets(config: InfraConfig) -> None:
        if config.services.postgres.enabled:
            variable = config.services.postgres.password_env
            if not os.environ.get(variable):
                raise MissingPrerequisiteError(
                    f"required environment variable {variable} is not set; "
                    "provide it in the environment without adding the password "
                    "to the config or generated files"
                )

    def _compose(
        self,
        config: InfraConfig,
        output: Path,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            [
                "docker",
                "compose",
                "--project-name",
                config.project.name,
                "--file",
                str(output / "compose.yaml"),
                *args,
            ],
            output,
        )

    def _ps(self, config: InfraConfig, output: Path) -> tuple[ServiceStatus, ...]:
        result = self._compose(config, output, "ps", "--all", "--format", "json")
        if result.returncode != 0:
            raise AdapterError(
                f"Docker Compose status check failed (exit code {result.returncode}); "
                "confirm the Docker daemon is running"
            )
        return _parse_compose_ps(result.stdout)

    def _has_project_containers(self, project_name: str) -> bool:
        result = self._run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ],
            Path.cwd(),
        )
        if result.returncode != 0:
            raise AdapterError(
                f"cannot verify existing Docker resources "
                f"(exit code {result.returncode}); refusing to adopt project"
            )
        return bool(result.stdout.strip())

    def _check_health(
        self,
        config: InfraConfig,
        services: tuple[ServiceStatus, ...],
    ) -> None:
        by_name: dict[str, list[ServiceStatus]] = {}
        for service in services:
            by_name.setdefault(service.name, []).append(service)
        problems: list[str] = []
        for expected in sorted(_expected_services(config)):
            instances = by_name.get(expected, [])
            if not instances:
                problems.append(f"{expected}: missing")
                continue
            for item in instances:
                if item.state != "running":
                    problems.append(f"{expected}: {item.state}")
                elif item.health not in {"not reported", "", "healthy"}:
                    problems.append(f"{expected}: {item.health}")
        if problems:
            raise AdapterError("service health check failed: " + "; ".join(problems))

    def apply(self, config: InfraConfig, config_dir: Path) -> RuntimeStatus:
        if config.runtime.type != "docker_compose":
            raise AdapterError("apply is supported only for docker_compose runtime")
        self._require_docker()
        self._require_config_secrets(config)

        existing_output = _output_dir(config, config_dir)
        previous_state = load_state(existing_output, config.project.name)

        generate_files(config, config_dir)
        output, manifest, compose_hash, config_hash = _load_generated_manifest(
            config, config_dir
        )
        compose_file = output / "compose.yaml"
        check = self._run(
            [
                "docker",
                "compose",
                "--project-name",
                config.project.name,
                "--file",
                str(compose_file),
                "config",
                "--quiet",
            ],
            output,
        )
        if check.returncode != 0:
            raise AdapterError(
                f"generated Compose configuration is invalid "
                f"(exit code {check.returncode}); check required environment "
                "variables and generated YAML"
            )

        if previous_state is None:
            if self._has_project_containers(config.project.name):
                raise AdapterError(
                    "containers with this Compose project name already exist but "
                    "are not recorded as managed; refusing to adopt them"
                )

        state = ManagedState(
            project_name=config.project.name,
            compose_project=config.project.name,
            compose_sha256=compose_hash,
            config_sha256=config_hash,
            status="applying",
        )
        save_state(output, state)
        up = self._compose(config, output, "up", "--detach")
        if up.returncode != 0:
            save_state(output, state.model_copy(update={"status": "apply_failed"}))
            raise AdapterError(
                f"Docker Compose apply failed (exit code {up.returncode}); "
                "partial resources may exist and are recorded for review; "
                "check the Docker daemon, image availability, and port conflicts"
            )

        try:
            services = self._ps(config, output)
            self._check_health(config, services)
        except AdapterError:
            save_state(output, state.model_copy(update={"status": "apply_failed"}))
            raise
        save_state(output, state.model_copy(update={"status": "applied"}))
        return RuntimeStatus(True, "applied", services)

    def status(self, config: InfraConfig, config_dir: Path) -> RuntimeStatus:
        if config.runtime.type != "docker_compose":
            return RuntimeStatus(False, "systemd is generate-only in this release")
        output = _output_dir(config, config_dir)
        state = load_state(output, config.project.name)
        if state is None:
            return RuntimeStatus(False, "no resources are managed by infra-builder")
        _, manifest, compose_hash, config_hash = _load_generated_manifest(
            config, config_dir
        )
        if (
            state.compose_sha256 != compose_hash
            or state.config_sha256 != config_hash
            or manifest["config_sha256"] != _config_sha256(config)
        ):
            raise AdapterError(
                "managed runtime state does not match the generated config; "
                "manual review is required"
            )
        self._require_docker()
        self._require_config_secrets(config)
        services = self._ps(config, output)
        return RuntimeStatus(True, state.status, services)

    def destroy_preview(self, config: InfraConfig, config_dir: Path) -> tuple[str, ...]:
        """Verify ownership and list exactly what destroy would stop/remove."""
        if config.runtime.type != "docker_compose":
            raise AdapterError("destroy is supported only for docker_compose runtime")
        output = _output_dir(config, config_dir)
        state = load_state(output, config.project.name)
        if state is None:
            return ()
        _, manifest, compose_hash, config_hash = _load_generated_manifest(
            config, config_dir
        )
        if (
            state.compose_sha256 != compose_hash
            or state.config_sha256 != config_hash
            or manifest["config_sha256"] != _config_sha256(config)
        ):
            raise AdapterError(
                "managed runtime state does not match the generated config; "
                "manual review is required"
            )
        services = sorted(_expected_services(config))
        files = sorted(manifest["managed_files"])
        return (
            f"Compose project: {state.compose_project}",
            "Services: " + ", ".join(services),
            "Managed generated files: " + ", ".join(files),
            f"Manifest: {MANIFEST_NAME}",
            "State file: .infra-builder-state.json",
            f"State: {state.status}",
            "Named volumes are retained; destroy does not pass --volumes.",
        )

    def destroy(self, config: InfraConfig, config_dir: Path) -> tuple[str, ...]:
        if config.runtime.type != "docker_compose":
            raise AdapterError("destroy is supported only for docker_compose runtime")
        output = _output_dir(config, config_dir)
        state = load_state(output, config.project.name)
        if state is None:
            return ("No infra-builder-managed resources found.",)

        _, manifest, compose_hash, config_hash = _load_generated_manifest(
            config, config_dir
        )
        if (
            state.compose_sha256 != compose_hash
            or state.config_sha256 != config_hash
            or manifest["config_sha256"] != _config_sha256(config)
        ):
            raise AdapterError(
                "managed runtime state does not match the generated config; "
                "manual review is required"
            )
        self._require_config_secrets(config)
        managed_files: dict[str, str] = manifest["managed_files"]
        manifest_path = output / MANIFEST_NAME
        try:
            original_manifest = manifest_path.read_bytes()
        except OSError as error:
            raise AdapterError(
                f"cannot verify managed manifest '{manifest_path}'"
            ) from error
        self._require_docker()
        state = state.model_copy(update={"status": "destroying"})
        save_state(output, state)
        down = self._compose(config, output, "down")
        if down.returncode != 0:
            save_state(output, state.model_copy(update={"status": "destroy_failed"}))
            raise AdapterError(
                f"Docker Compose destroy failed (exit code {down.returncode}); "
                "resources remain recorded for review; confirm the Docker daemon "
                "is running"
            )

        managed_paths: list[Path] = []
        for relative_path, expected_hash in managed_files.items():
            path = output / relative_path
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError) as error:
                save_state(
                    output, state.model_copy(update={"status": "destroy_failed"})
                )
                raise AdapterError(
                    f"cannot safely resolve managed file '{relative_path}'"
                ) from error
            if path.is_symlink() or not resolved.is_relative_to(output):
                save_state(
                    output, state.model_copy(update={"status": "destroy_failed"})
                )
                raise AdapterError(
                    f"refusing to remove unsafe managed file '{relative_path}'"
                )
            if not path.is_file() or _sha256(path.read_bytes()) != expected_hash:
                save_state(
                    output, state.model_copy(update={"status": "destroy_failed"})
                )
                raise AdapterError(
                    f"refusing to remove modified or missing managed file "
                    f"'{relative_path}'"
                )
            managed_paths.append(path)

        try:
            if manifest_path.read_bytes() != original_manifest:
                save_state(
                    output, state.model_copy(update={"status": "destroy_failed"})
                )
                raise AdapterError(
                    "managed manifest changed during destroy; manual review is required"
                )
        except OSError as error:
            save_state(output, state.model_copy(update={"status": "destroy_failed"}))
            raise AdapterError(
                f"cannot verify managed manifest '{manifest_path}' after destroy"
            ) from error

        for path in sorted(managed_paths, reverse=True):
            path.unlink()
        manifest_path.unlink()
        remove_state(output)
        return (
            f"Stopped Compose project {config.project.name}.",
            "Removed unchanged tool-managed generated files and manifest.",
            "Named volumes were retained.",
        )

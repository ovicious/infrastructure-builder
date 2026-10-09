"""Safe, deterministic generation of local project files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from infra_builder.models import InfraConfig
from infra_builder.planner import MANIFEST_NAME, planned_files
from infra_builder.validator import validate_config

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class GenerationError(RuntimeError):
    """Raised when files cannot be generated safely."""


def _canonical_config(config: InfraConfig) -> bytes:
    return json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _compose_document(config: InfraConfig) -> dict[str, Any]:
    application = config.application
    services: dict[str, Any] = {
        application.name: {
            "image": application.image,
            "ports": [f"127.0.0.1:{application.port}:{application.port}"],
            "restart": "unless-stopped",
        }
    }

    if config.services.postgres.enabled:
        postgres = config.services.postgres
        services["postgres"] = {
            "image": "postgres:16",
            "restart": "unless-stopped",
            "environment": {
                "POSTGRES_DB": postgres.database,
                "POSTGRES_USER": postgres.username,
                "POSTGRES_PASSWORD": (
                    "${"
                    f"{postgres.password_env}:?"
                    f"Set {postgres.password_env} before starting services}}"
                ),
            },
            "volumes": ["postgres_data:/var/lib/postgresql/data"],
        }

    if config.services.redis.enabled:
        services["redis"] = {
            "image": "redis:7-alpine",
            "restart": "unless-stopped",
        }

    if config.observability.metrics:
        services["prometheus"] = {
            "image": "prom/prometheus:v2.54.1",
            "restart": "unless-stopped",
            "ports": ["127.0.0.1:9090:9090"],
            "volumes": [
                "./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro"
            ],
        }

    document: dict[str, Any] = {
        "name": config.project.name,
        "services": services,
    }
    if config.services.postgres.enabled:
        document["volumes"] = {"postgres_data": {}}
    return document


def _prometheus_config(config: InfraConfig) -> dict[str, Any]:
    return {
        "global": {"scrape_interval": "15s"},
        "scrape_configs": [
            {
                "job_name": config.application.name,
                "metrics_path": "/metrics",
                "static_configs": [
                    {
                        "targets": [
                            f"{config.application.name}:{config.application.port}"
                        ]
                    }
                ],
            }
        ],
    }


def _systemd_unit(config: InfraConfig) -> str:
    app = config.application.name
    return (
        "# Template only: review and set the executable and working directory "
        "before installation.\n"
        "# This file is not installed, enabled, or started by infra-builder.\n"
        "[Unit]\n"
        f"Description={config.project.name} application\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/bin/env {app}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_files(config: InfraConfig) -> dict[str, bytes]:
    """Render every generated file without touching the filesystem."""
    files: dict[str, bytes] = {}
    if config.runtime.type == "docker_compose":
        files["compose.yaml"] = yaml.safe_dump(
            _compose_document(config),
            sort_keys=False,
            default_flow_style=False,
        ).encode("utf-8")
        if config.services.postgres.enabled:
            files[".env.example"] = (
                "# Set this variable in your shell or a private, untracked .env file.\n"
                f"# Required variable: {config.services.postgres.password_env}\n"
            ).encode()
        if config.observability.metrics:
            files["monitoring/prometheus.yml"] = yaml.safe_dump(
                _prometheus_config(config),
                sort_keys=False,
                default_flow_style=False,
            ).encode("utf-8")
    else:
        files[f"systemd/{config.application.name}.service"] = _systemd_unit(
            config
        ).encode("utf-8")

    file_hashes = {name: _sha256(contents) for name, contents in files.items()}
    manifest = {
        "schema_version": 1,
        "project_name": config.project.name,
        "config_sha256": _sha256(_canonical_config(config)),
        "managed_files": file_hashes,
    }
    files[MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return files


def _read_manifest(path: Path, project_name: str) -> dict[str, str] | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise GenerationError(f"managed manifest is not a regular file: '{path}'")

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GenerationError(
            f"cannot read managed manifest '{path}'; manual review is required"
        ) from error

    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("project_name") != project_name
        or not isinstance(manifest.get("config_sha256"), str)
        or not _SHA256_PATTERN.fullmatch(manifest["config_sha256"])
        or not isinstance(manifest.get("managed_files"), dict)
    ):
        raise GenerationError(
            f"managed manifest '{path}' is invalid or belongs to another project"
        )

    managed_files = manifest["managed_files"]
    for name, digest in managed_files.items():
        candidate = Path(name)
        if (
            not isinstance(name, str)
            or candidate.is_absolute()
            or ".." in candidate.parts
            or not isinstance(digest, str)
            or not _SHA256_PATTERN.fullmatch(digest)
        ):
            raise GenerationError(
                f"managed manifest '{path}' contains an invalid file entry"
            )
    return managed_files


def _validate_target_path(root: Path, relative_path: str) -> Path:
    candidate = root / relative_path
    if candidate.is_symlink():
        raise GenerationError(f"refusing to replace a symlink: '{candidate}'")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as error:
        raise GenerationError(
            f"cannot safely resolve generated path '{candidate}': {error}"
        ) from error
    if not resolved.is_relative_to(root):
        raise GenerationError(
            f"generated path escapes the project output directory: '{relative_path}'"
        )
    return candidate


def _write_atomic(path: Path, contents: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return path


def generate_files(config: InfraConfig, config_dir: Path) -> tuple[Path, ...]:
    """Generate files only after validating ownership and collision safety."""
    validate_config(config, config_dir)
    try:
        config_root = config_dir.resolve()
        output_root = (config_root / config.project.output_dir).resolve()
    except (OSError, RuntimeError) as error:
        raise GenerationError(
            f"cannot safely resolve project output directory: {error}"
        ) from error
    if not output_root.is_relative_to(config_root):
        raise GenerationError("project output directory escapes the config directory")
    if output_root.exists() and not output_root.is_dir():
        raise GenerationError(
            f"project output path is not a directory: '{output_root}'"
        )

    contents_by_path = render_files(config)
    expected_files = set(planned_files(config))
    if set(contents_by_path) != expected_files:
        raise GenerationError("generated files do not match the deterministic plan")

    manifest_path = _validate_target_path(output_root, MANIFEST_NAME)
    previous_hashes = _read_manifest(manifest_path, config.project.name)
    target_paths: dict[str, Path] = {}
    original_contents: dict[str, bytes | None] = {}

    for name, _contents in contents_by_path.items():
        path = _validate_target_path(output_root, name)
        target_paths[name] = path
        old_hash = previous_hashes.get(name) if previous_hashes is not None else None

        if path.exists():
            if not path.is_file():
                raise GenerationError(f"refusing to replace a non-file: '{path}'")
            current_contents = path.read_bytes()
            current_hash = _sha256(current_contents)
            if old_hash is None:
                if name != MANIFEST_NAME or previous_hashes is None:
                    raise GenerationError(
                        f"refusing to overwrite unmanaged file '{path}'"
                    )
            elif current_hash != old_hash:
                raise GenerationError(
                    f"managed file '{path}' was modified; manual review is required"
                )
            original_contents[name] = current_contents
        else:
            if old_hash is not None and name != MANIFEST_NAME:
                original_contents[name] = None
            else:
                original_contents[name] = None

    if previous_hashes is not None:
        unexpected_managed = set(previous_hashes) - set(contents_by_path)
        if unexpected_managed:
            names = ", ".join(sorted(unexpected_managed))
            raise GenerationError(
                f"existing managed files are no longer generated ({names}); "
                "manual review is required"
            )

    written: list[str] = []
    try:
        for name, contents in contents_by_path.items():
            _write_atomic(target_paths[name], contents)
            written.append(name)
    except OSError as error:
        for name in reversed(written):
            old_contents = original_contents[name]
            path = target_paths[name]
            try:
                if old_contents is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_atomic(path, old_contents)
            except OSError as rollback_error:
                raise GenerationError(
                    f"generation failed at '{path}' and rollback failed: "
                    f"{rollback_error}"
                ) from error
        raise GenerationError(f"cannot write generated files: {error}") from error

    return tuple(target_paths[name] for name in contents_by_path)

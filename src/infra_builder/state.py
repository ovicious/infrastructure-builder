"""Persistent ownership records for resources started by infra-builder."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from infra_builder.generator import GenerationError
from infra_builder.planner import STATE_NAME

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ManagedState(BaseModel):
    """Identity and lifecycle of one tool-managed Compose project."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    project_name: str
    compose_project: str
    compose_sha256: str
    config_sha256: str
    status: Literal[
        "applying",
        "applied",
        "apply_failed",
        "destroying",
        "destroy_failed",
    ]

    @field_validator("project_name", "compose_project")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        if not value or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value
        ):
            raise ValueError("invalid project name in managed state")
        return value

    @field_validator("compose_sha256", "config_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("invalid hash in managed state")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported state schema version")
        return value


def state_path(output_dir: Path) -> Path:
    return output_dir / STATE_NAME


def load_state(output_dir: Path, project_name: str) -> ManagedState | None:
    path = state_path(output_dir)
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise GenerationError(f"managed state is not a regular file: '{path}'")
    try:
        state = ManagedState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        raise GenerationError(
            f"managed state '{path}' is corrupt; manual review is required"
        ) from error
    if state.project_name != project_name or state.compose_project != project_name:
        raise GenerationError(f"managed state '{path}' belongs to a different project")
    return state


def save_state(output_dir: Path, state: ManagedState) -> None:
    path = state_path(output_dir)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GenerationError(f"refusing to replace non-file state path '{path}'")
    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATE_NAME}.",
        suffix=".tmp",
        dir=output_dir,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(state.model_dump_json(indent=2) + "\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def remove_state(output_dir: Path) -> None:
    path = state_path(output_dir)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GenerationError(f"refusing to remove non-file state path '{path}'")
    path.unlink(missing_ok=True)

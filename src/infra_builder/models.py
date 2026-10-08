"""Typed configuration models and deterministic input validation."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_PATTERN = re.compile(r"^(?=.{1,63}$)[a-z](?:[a-z0-9-]*[a-z0-9])?$")
_DATABASE_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ENV_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(StrictModel):
    name: str = "infra-project"
    output_dir: str = "./generated/infra-project"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "must start with a lowercase letter and contain only "
                "lowercase letters, digits, or hyphens (up to 63 characters)"
            )
        return value

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ValueError("must be a relative path without '..' segments")
        return value


class InfrastructureConfig(StrictModel):
    target: Literal["local"] = "local"
    os: str = "ubuntu"

    @field_validator("os")
    @classmethod
    def validate_os(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class RuntimeConfig(StrictModel):
    type: Literal["docker_compose", "host_systemd"] = "docker_compose"


class ApplicationConfig(StrictModel):
    type: Literal["generic", "python", "go", "static"] = "generic"
    name: str = "app"
    port: int = Field(default=8080, strict=True, ge=1, le=65535)
    image: str | None = "nginx:stable"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError("must be a valid lowercase service name")
        return value

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(c.isspace() for c in value)):
            raise ValueError("must be non-empty and contain no whitespace")
        return value


class PostgresConfig(StrictModel):
    enabled: bool = False
    database: str = "app"
    username: str = "app_user"
    password_env: str = "INFRA_BUILDER_POSTGRES_PASSWORD"

    @field_validator("database", "username")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _DATABASE_IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("must be a valid lowercase service identifier")
        return value

    @field_validator("password_env")
    @classmethod
    def validate_password_env(cls, value: str) -> str:
        if not _ENV_PATTERN.fullmatch(value):
            raise ValueError("must be a valid environment variable name")
        return value


class RedisConfig(StrictModel):
    enabled: bool = False


class ServicesConfig(StrictModel):
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)


class ObservabilityConfig(StrictModel):
    metrics: bool = False
    centralized_logs: bool = False


class ExecutionConfig(StrictModel):
    require_approval: Literal[True] = True


class InfraConfig(StrictModel):
    schema_version: Literal[1] = 1
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    infrastructure: InfrastructureConfig = Field(default_factory=InfrastructureConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    application: ApplicationConfig = Field(default_factory=ApplicationConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported schema version; expected integer 1")
        return value

    @model_validator(mode="after")
    def require_image_for_compose(self) -> InfraConfig:
        if self.runtime.type == "docker_compose" and self.application.image is None:
            raise ValueError("Docker Compose applications require an image")
        return self

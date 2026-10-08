"""Read and write versioned YAML configuration."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from infra_builder.models import InfraConfig


class ConfigError(ValueError):
    """Raised when a configuration file cannot be loaded or validated."""


def load_config(path: Path) -> InfraConfig:
    """Load and validate a configuration file without making system changes."""
    try:
        with path.open(encoding="utf-8") as config_file:
            data: Any = yaml.safe_load(config_file)
    except OSError as error:
        raise ConfigError(f"cannot read config '{path}': {error.strerror}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in '{path}': {error}") from error

    if not isinstance(data, dict):
        raise ConfigError(f"config '{path}' must contain a YAML mapping")

    try:
        return InfraConfig.model_validate(data)
    except ValidationError as error:
        details = "\n".join(
            f"  - {'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise ConfigError(f"invalid config '{path}':\n{details}") from error


def save_config(config: InfraConfig, path: Path) -> None:
    """Write a validated configuration as readable YAML."""
    try:
        with path.open("w", encoding="utf-8") as config_file:
            yaml.safe_dump(
                config.model_dump(mode="json"),
                config_file,
                sort_keys=False,
                default_flow_style=False,
            )
    except OSError as error:
        raise ConfigError(f"cannot write config '{path}': {error.strerror}") from error

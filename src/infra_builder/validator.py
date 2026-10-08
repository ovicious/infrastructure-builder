"""Configuration checks that depend on the selected config file location."""

from pathlib import Path

from infra_builder.config_io import ConfigError
from infra_builder.models import InfraConfig


def validate_config(config: InfraConfig, base_dir: Path) -> None:
    """Reject output paths that resolve outside the config directory."""
    base = base_dir.resolve()
    output_dir = (base / config.project.output_dir).resolve()
    if not output_dir.is_relative_to(base):
        raise ConfigError(
            f"project.output_dir resolves outside the config directory: "
            f"'{config.project.output_dir}'"
        )
